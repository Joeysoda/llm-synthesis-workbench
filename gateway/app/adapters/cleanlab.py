from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from cleanlab import Datalab
from cleanlab.filter import find_label_issues
from cleanlab.rank import get_label_quality_scores
from pydantic import BaseModel, Field, model_validator
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import LabelEncoder, normalize

from ..config import Settings
from ..contracts import BehaviorHints, ExecutionContext, RiskLevel, ToolError, ToolResult, build_tool
from ..database import Database
from ..security import ensure_within


class CleanlabAuditInput(BaseModel):
    source_type: str = Field(pattern="^(asset|job)$")
    asset_id: str | None = None
    source_job_id: str | None = None
    pred_probs_asset_id: str | None = None
    id_column: str = "id"
    text_column: str = "text"
    label_column: str = "label"

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_type == "asset" and not self.asset_id:
            raise ValueError("上传检查需要 asset_id")
        if self.source_type == "job" and not self.source_job_id:
            raise ValueError("任务导入需要 source_job_id")
        return self


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if value != value:
        return None
    return value


def _read_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    if suffix == ".jsonl":
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL 第 {line_number} 行不是对象")
            rows.append(value)
        return rows
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value = value.get("rows") or value.get("data") or []
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ValueError("JSON 必须是对象数组")
        return value
    raise ValueError("Cleanlab 只支持 CSV、JSON 或 JSONL")


def _read_uploaded_probabilities(
    path: Path, row_ids: list[str], classes: list[str]
) -> np.ndarray:
    rows = _read_records(path)
    by_id = {str(row.get("id", "")): row for row in rows}
    if set(by_id) != set(row_ids):
        raise ValueError("预测概率 ID 必须与数据集完整对应")
    matrix = []
    for row_id in row_ids:
        row = by_id[row_id]
        value = row.get("probabilities")
        if isinstance(value, dict):
            if set(value) != set(classes):
                raise ValueError(f"样本 {row_id} 的 probabilities 必须覆盖全部标签")
            probabilities = [float(value[label]) for label in classes]
        else:
            uploaded_classes = row.get("classes")
            values = row.get("pred_probs")
            if (
                not isinstance(uploaded_classes, list)
                or not isinstance(values, list)
                or set(str(item) for item in uploaded_classes) != set(classes)
                or len(values) != len(classes)
            ):
                raise ValueError(
                    f"样本 {row_id} 需要 probabilities 对象，或 classes 与 pred_probs 数组"
                )
            indexed = {str(label): float(number) for label, number in zip(uploaded_classes, values)}
            probabilities = [indexed[label] for label in classes]
        if any(number < 0 or number > 1 for number in probabilities):
            raise ValueError(f"样本 {row_id} 的概率必须在 0 到 1 之间")
        if abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError(f"样本 {row_id} 的概率和必须为 1")
        matrix.append(probabilities)
    return np.asarray(matrix, dtype=float)


def _feature_matrix(texts: list[str]) -> tuple[Any, np.ndarray]:
    vectorizer = TfidfVectorizer(
        analyzer="char", ngram_range=(2, 4), min_df=1, max_features=10_000, sublinear_tf=True
    )
    sparse = vectorizer.fit_transform(texts)
    if sparse.shape[1] < 2:
        raise ValueError("文本内容过少，无法生成有效特征")
    dimensions = min(64, sparse.shape[0] - 1, sparse.shape[1] - 1)
    if dimensions < 2:
        dense = sparse.toarray()
    else:
        dense = TruncatedSVD(n_components=dimensions, random_state=42).fit_transform(sparse)
    return sparse, normalize(dense)


def _issue_frame(lab: Datalab, name: str, size: int) -> list[dict[str, Any]]:
    try:
        frame = lab.get_issues(name)
    except Exception:
        return [{} for _ in range(size)]
    records = frame.reset_index(drop=True).to_dict(orient="records")
    return [_json_safe(row) for row in records]


def build_cleanlab_tools(database: Database, settings: Settings) -> list:
    async def audit(params: CleanlabAuditInput, ctx: ExecutionContext) -> ToolResult:
        await ctx.emit("读取并校验输入数据", "stage", {"stage_id": "parse"})
        try:
            if params.source_type == "asset":
                asset = database.get_asset(params.asset_id or "")
                if not asset or asset["project_id"] != ctx.project_id:
                    raise ValueError("数据文件不属于当前 Cleanlab 项目")
                source_path = ensure_within(Path(asset["path"]), settings.runtime_root)
                raw_rows = _read_records(source_path)
                required = {params.id_column, params.text_column, params.label_column}
                if not raw_rows or not required.issubset(raw_rows[0]):
                    missing = sorted(required - set(raw_rows[0] if raw_rows else {}))
                    raise ValueError(f"数据缺少列：{', '.join(missing) or '无可读取记录'}")
                rows = [
                    {
                        "id": str(row[params.id_column]),
                        "text": str(row[params.text_column]).strip(),
                        "label": str(row[params.label_column]).strip(),
                        "raw": row,
                    }
                    for row in raw_rows
                ]
                source_name = asset["filename"]
                has_labels = True
            else:
                source_run = database.get_run(params.source_job_id or "")
                if not source_run or source_run["status"] != "succeeded":
                    raise ValueError("只能导入已完成的平台任务")
                imported = database.list_samples(source_run["id"])
                if not imported:
                    raise ValueError("来源任务没有可检查样本")
                rows = [
                    {
                        "id": sample["id"],
                        "text": "\n".join(
                            part for part in (sample["question"], sample["answer"], sample["reasoning"]) if part
                        ),
                        "label": "",
                        "raw": sample,
                    }
                    for sample in imported
                ]
                source_name = f"平台任务 {source_run['id']}"
                has_labels = False
            if not 20 <= len(rows) <= 5000:
                raise ValueError("数据量必须在 20 到 5000 条之间")
            if len({row["id"] for row in rows}) != len(rows):
                raise ValueError("ID 列存在重复值")
            if any(not row["text"] for row in rows):
                raise ValueError("文本列不能包含空值")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return ToolResult(
                success=False,
                summary="Cleanlab 输入校验失败",
                error=ToolError(code="cleanlab_invalid_input", message=str(exc)),
            )

        await ctx.emit("构建中文文本特征", "stage", {"stage_id": "features"})
        texts = [row["text"] for row in rows]
        try:
            sparse_features, dense_features = _feature_matrix(texts)
            pred_probs = None
            class_names: list[str] = []
            encoded_labels = None
            if has_labels:
                label_counts = Counter(row["label"] for row in rows)
                if len(label_counts) < 2:
                    raise ValueError("至少需要两个标签")
                if min(label_counts.values()) < 2:
                    raise ValueError("每个标签至少需要两条数据")
                encoder = LabelEncoder().fit([row["label"] for row in rows])
                encoded_labels = encoder.transform([row["label"] for row in rows])
                class_names = [str(value) for value in encoder.classes_]
                if params.pred_probs_asset_id:
                    probability_asset = database.get_asset(params.pred_probs_asset_id)
                    if not probability_asset or probability_asset["project_id"] != ctx.project_id:
                        raise ValueError("预测概率文件不属于当前 Cleanlab 项目")
                    probability_path = ensure_within(Path(probability_asset["path"]), settings.runtime_root)
                    pred_probs = _read_uploaded_probabilities(
                        probability_path, [row["id"] for row in rows], class_names
                    )
                    probability_source = "uploaded"
                else:
                    folds = min(5, min(label_counts.values()))
                    model = LogisticRegression(max_iter=1500, class_weight="balanced", random_state=42)
                    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)
                    pred_probs = cross_val_predict(
                        model, sparse_features, encoded_labels, cv=splitter, method="predict_proba"
                    )
                    probability_source = f"local-{folds}-fold-cv"
            else:
                probability_source = "not-applicable"
        except ValueError as exc:
            return ToolResult(
                success=False,
                summary="Cleanlab 特征或标签校验失败",
                error=ToolError(code="cleanlab_feature_error", message=str(exc)),
            )

        await ctx.emit("运行 Cleanlab 数据质量检查", "stage", {"stage_id": "audit"})
        try:
            # Datalab 的标签检查默认会启动多进程；网关任务运行在线程池中，
            # 这里改用 Cleanlab 的同源底层接口并固定 n_jobs=1，避免 macOS
            # spawn 子进程时重复加载服务入口。异常和近重复仍由 Datalab 处理。
            if has_labels:
                label_scores = get_label_quality_scores(encoded_labels, pred_probs)
                ranked_indices = find_label_issues(
                    encoded_labels,
                    pred_probs,
                    return_indices_ranked_by="self_confidence",
                    n_jobs=1,
                )
                issue_index_set = {int(value) for value in ranked_indices}
                predicted_indices = np.asarray(pred_probs).argmax(axis=1)
                label_issues = [
                    {
                        "label_score": float(label_scores[index]),
                        "is_label_issue": index in issue_index_set,
                        "predicted_label": int(predicted_indices[index]),
                    }
                    for index in range(len(rows))
                ]
            else:
                label_issues = [{} for _ in rows]
            lab = Datalab(data={"text": texts})
            lab.find_issues(
                features=dense_features,
                issue_types={"outlier": {}, "near_duplicate": {}},
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                summary="Cleanlab 检查执行失败",
                error=ToolError(code="cleanlab_audit_failed", message=str(exc)),
            )

        outlier_issues = _issue_frame(lab, "outlier", len(rows))
        duplicate_issues = _issue_frame(lab, "near_duplicate", len(rows))
        await ctx.emit("整理问题、分数与审核记录", "stage", {"stage_id": "export"})
        samples = []
        issue_rows = []
        for index, row in enumerate(rows):
            label_item = label_issues[index]
            outlier_item = outlier_issues[index]
            duplicate_item = duplicate_issues[index]
            predicted_index = label_item.get("predicted_label")
            predicted_label = (
                class_names[int(predicted_index)]
                if has_labels and predicted_index is not None
                else ""
            )
            quality = {
                "rule_passed": not bool(label_item.get("is_label_issue", False)),
                "human_status": "pending",
                "decision": "pending",
                "original_label": row["label"],
                "suggested_label": predicted_label,
                "label_score": label_item.get("label_score"),
                "is_label_issue": bool(label_item.get("is_label_issue", False)),
                "outlier_score": outlier_item.get("outlier_score"),
                "is_outlier_issue": bool(outlier_item.get("is_outlier_issue", False)),
                "near_duplicate_score": duplicate_item.get("near_duplicate_score"),
                "is_near_duplicate_issue": bool(
                    duplicate_item.get("is_near_duplicate_issue", False)
                ),
                "near_duplicate_sets": duplicate_item.get("near_duplicate_sets") or [],
            }
            issue_rows.append({"id": row["id"], "text": row["text"], **quality})
            reasons = []
            if quality["is_label_issue"]:
                reasons.append(f"原标签可能有误，建议复核为“{predicted_label}”")
            if quality["is_outlier_issue"]:
                reasons.append("文本与大多数样本差异较大")
            if quality["is_near_duplicate_issue"]:
                reasons.append("存在完全重复或近重复文本")
            samples.append(
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": "data_quality_audit",
                    "question": row["text"],
                    "answer": row["label"],
                    "reasoning": "；".join(reasons) or "未发现当前启用检查支持的明显问题",
                    "source": {
                        "file": source_name,
                        "row_id": row["id"],
                        "original_label": row["label"],
                        "raw_record": row["raw"],
                        "source_job_id": params.source_job_id,
                    },
                    "generation": {
                        "tool": "cleanlab",
                        "version": "2.9.0",
                        "model": "TF-IDF + LogisticRegression",
                        "probability_source": probability_source,
                    },
                    "quality": quality,
                }
            )
        artifact = ctx.run_dir / "cleanlab-audit.jsonl"
        artifact.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in issue_rows) + "\n",
            encoding="utf-8",
        )
        summary = {
            "row_count": len(rows),
            "has_labels": has_labels,
            "label_issues": sum(bool(row.get("is_label_issue")) for row in label_issues),
            "outliers": sum(bool(row.get("is_outlier_issue")) for row in outlier_issues),
            "near_duplicates": sum(
                bool(row.get("is_near_duplicate_issue")) for row in duplicate_issues
            ),
            "classes": class_names,
            "samples": samples,
        }
        return ToolResult(
            success=True,
            data=summary,
            summary=(
                f"Cleanlab 已检查 {len(rows)} 条数据："
                f"可疑标签 {summary['label_issues']} 条，异常 {summary['outliers']} 条，"
                f"近重复 {summary['near_duplicates']} 条"
            ),
            persisted_path=str(artifact),
        )

    return [
        build_tool(
            name="cleanlab_audit_dataset",
            description="使用本机 Cleanlab、交叉验证模型和文本特征检查标签、异常与近重复数据。",
            short_description="检查文本分类数据质量",
            input_schema=CleanlabAuditInput,
            execute=audit,
            tags=["data-quality", "label-audit", "dataset-construction"],
            hints=BehaviorHints(read_only=False, destructive=False, idempotent=True),
            risk_level=RiskLevel.MEDIUM,
            timeout_ms=600_000,
            is_available=lambda: True,
        )
    ]
