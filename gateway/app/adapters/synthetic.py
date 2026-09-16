from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..config import Settings
from ..contracts import (
    BehaviorHints,
    ExecutionContext,
    RiskLevel,
    ToolError,
    ToolResult,
    ToolTier,
    build_tool,
)
from ..database import Database
from ..security import ensure_within
from .process import command_failed, run_command


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


class SyntheticIngestInput(BaseModel):
    asset_id: str
    output_name: str | None = None


class SyntheticGenerateInput(BaseModel):
    asset_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list, max_length=50)
    content_type: Literal[
        "cot", "qa", "summary", "cot-enhance", "multimodal-qa"
    ] = "cot"
    num_pairs: int = Field(default=30, ge=1, le=100)
    min_retained: int = Field(default=20, ge=1, le=100)
    chunk_size: int = Field(default=4000, ge=500, le=20000)
    chunk_overlap: int = Field(default=200, ge=0, le=2000)
    threshold: float = Field(default=7.0, ge=1, le=10)
    model: str | None = None


class SyntheticCurateInput(BaseModel):
    source_run_id: str
    threshold: float = Field(default=7.0, ge=1, le=10)
    format: Literal["jsonl", "alpaca", "ft", "chatml"] = "jsonl"


def write_config(
    path: Path,
    run_dir: Path,
    settings: Settings,
    model: str,
    *,
    chunk_size: int = 4000,
    overlap: int = 200,
    num_pairs: int = 30,
    threshold: float = 7.0,
) -> None:
    default_path = settings.synthetic_repo / "configs" / "config.yaml"
    data = copy.deepcopy(yaml.safe_load(default_path.read_text(encoding="utf-8")))
    data["paths"] = {
        "input": str(run_dir / "input"),
        "output": {
            "parsed": str(run_dir / "parsed"),
            "generated": str(run_dir / "generated"),
            "curated": str(run_dir / "curated"),
            "final": str(run_dir / "final"),
        },
    }
    data["llm"]["provider"] = "api-endpoint"
    data["api-endpoint"].update(
        {
            "api_base": settings.llm_proxy_base_url,
            "api_key": "",
            "model": model,
            "max_retries": 3,
            "retry_delay": 1.0,
        }
    )
    data["generation"].update(
        {
            "temperature": 0.4,
            "top_p": 0.9,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "max_tokens": 8192,
            "num_pairs": num_pairs,
            "num_cot_examples": num_pairs,
            "batch_size": 8,
        }
    )
    data["curate"].update({"threshold": threshold, "batch_size": 5})
    data["format"].update(
        {"default": "jsonl", "include_metadata": True, "pretty_json": True}
    )
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def parse_json_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("qa_pairs", "cot_examples", "examples", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    return []


def cot_enhance_records(value: Any) -> list[dict[str, str]]:
    """Normalize supported QA/conversation JSON into question-answer records."""
    if isinstance(value, dict) and isinstance(value.get("qa_pairs"), list):
        records = []
        for pair in value["qa_pairs"]:
            if not isinstance(pair, dict):
                continue
            question = pair.get("question")
            answer = pair.get("answer")
            if isinstance(question, str) and question.strip() and isinstance(answer, str) and answer.strip():
                records.append(
                    {
                        "question": question.strip(),
                        "answer": answer.strip(),
                        "reasoning": str(pair.get("reasoning", "")).strip(),
                    }
                )
        return records

    conversations: list[Any] = []
    if isinstance(value, dict) and isinstance(value.get("conversations"), list):
        conversations = [value["conversations"]]
    elif isinstance(value, list):
        if value and all(
            isinstance(item, dict) and isinstance(item.get("conversations"), list)
            for item in value
        ):
            conversations = [item["conversations"] for item in value]
        elif value and all(
            isinstance(item, dict)
            and ("role" in item or "from" in item)
            and ("content" in item or "value" in item)
            for item in value
        ):
            conversations = [value]
        elif value and all(isinstance(item, list) for item in value):
            conversations = value

    records = []
    for messages in conversations:
        while (
            isinstance(messages, list)
            and len(messages) == 1
            and isinstance(messages[0], list)
        ):
            messages = messages[0]
        if not isinstance(messages, list):
            continue
        question = ""
        answer = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", message.get("from", ""))).lower()
            content = message.get("content", message.get("value", ""))
            if not isinstance(content, str) or not content.strip():
                continue
            if role in {"user", "human"}:
                question = content.strip()
            elif role in {"assistant", "gpt"} and question:
                answer = content.strip()
        if question and answer:
            records.append(
                {
                    "question": question,
                    "answer": answer,
                    "reasoning": "",
                }
            )
    return records


def prepare_source(source: Path, run_dir: Path) -> Path:
    if source.suffix.lower() != ".md":
        return source
    target_dir = run_dir / "input"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}.txt"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def build_synthetic_tools(database: Database, settings: Settings) -> list:
    commit = git_commit(settings.synthetic_repo)

    def environment() -> dict[str, str]:
        return {
            "API_ENDPOINT_KEY": "internal-proxy",
            "PYTHONUNBUFFERED": "1",
        }

    async def ingest(
        params: SyntheticIngestInput, ctx: ExecutionContext
    ) -> ToolResult:
        asset = database.get_asset(params.asset_id)
        if not asset or asset["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="找不到输入资产",
                error=ToolError(code="asset_not_found", message="Asset not found"),
            )
        source = prepare_source(
            ensure_within(Path(asset["path"]), settings.runtime_root), ctx.run_dir
        )
        parsed = ctx.run_dir / "parsed"
        parsed.mkdir(parents=True, exist_ok=True)
        config = ctx.run_dir / "config.yaml"
        write_config(config, ctx.run_dir, settings, settings.llm_model)
        output_name = params.output_name or f"asset_{params.asset_id[:8]}"
        result = await run_command(
            [
                settings.synthetic_cli,
                "--config",
                config,
                "ingest",
                source,
                "--output-dir",
                parsed,
                "--name",
                output_name,
            ],
            cwd=ctx.run_dir,
            ctx=ctx,
            env=environment(),
            timeout_seconds=180,
        )
        candidates = sorted(parsed.glob(f"{output_name}*"))
        if command_failed(result) or not candidates:
            return ToolResult(
                success=False,
                summary="Synthetic Data Kit 文档解析失败",
                error=ToolError(code="synthetic_ingest_failed", message="No parsed artifact"),
            )
        return ToolResult(
            success=True,
            data={"asset_id": params.asset_id, "parsed_path": str(candidates[-1])},
            summary=f"已解析 {asset['filename']}",
            persisted_path=str(candidates[-1]),
        )

    async def generate(
        params: SyntheticGenerateInput, ctx: ExecutionContext
    ) -> ToolResult:
        if params.content_type == "multimodal-qa" and not settings.vision_ready:
            return ToolResult(
                success=False,
                summary="视觉模型尚未配置",
                error=ToolError(
                    code="vision_not_configured",
                    message="请先配置 VISION_BASE_URL、VISION_API_KEY 和 VISION_MODEL",
                ),
            )
        if not settings.llm_ready:
            return ToolResult(
                success=False,
                summary="LLM 凭据尚未完成轮换确认",
                error=ToolError(
                    code="llm_credential_not_ready",
                    message=(
                        "请撤销旧密钥，设置新的 MINIMAX_API_KEY，"
                        "并显式设置 LLM_CREDENTIAL_ROTATED=true"
                    ),
                ),
            )
        asset_ids = list(dict.fromkeys(params.asset_ids or ([params.asset_id] if params.asset_id else [])))
        assets = [database.get_asset(asset_id) for asset_id in asset_ids]
        if not assets or any(
            not asset or asset["project_id"] != ctx.project_id for asset in assets
        ):
            return ToolResult(
                success=False,
                summary="找不到输入资产",
                error=ToolError(code="asset_not_found", message="Asset not found"),
            )
        valid_assets = [asset for asset in assets if asset]
        if params.content_type == "cot-enhance" and any(
            Path(asset["filename"]).suffix.lower() != ".json" for asset in valid_assets
        ):
            return ToolResult(
                success=False,
                summary="CoT 增强需要 JSON 对话文件",
                error=ToolError(
                    code="cot_enhance_requires_json",
                    message="请上传包含 conversations 或 qa_pairs 的 JSON 文件",
                ),
            )
        model = params.model or settings.llm_model
        config = ctx.run_dir / "config.yaml"
        write_config(
            config,
            ctx.run_dir,
            settings,
            model,
            chunk_size=params.chunk_size,
            overlap=params.chunk_overlap,
            num_pairs=params.num_pairs,
            threshold=params.threshold,
        )
        for name in ("parsed", "generated", "curated", "final"):
            (ctx.run_dir / name).mkdir(parents=True, exist_ok=True)

        await ctx.emit(
            "检查文本模型和命令行环境",
            "stage",
            {"stage_id": "check"},
        )
        check = await run_command(
            [
                settings.synthetic_cli,
                "--config",
                config,
                "system-check",
                "--provider",
                "api-endpoint",
                "--api-base",
                settings.llm_proxy_base_url,
            ],
            cwd=ctx.run_dir,
            ctx=ctx,
            env=environment(),
            timeout_seconds=90,
        )
        if command_failed(check):
            return ToolResult(
                success=False,
                summary="模型端点检查失败",
                error=ToolError(code="llm_system_check_failed", message="LLM endpoint rejected request"),
            )

        await ctx.emit(
            "解析输入文件",
            "stage",
            {"stage_id": "ingest"},
        )
        create_input: Path
        if params.content_type == "cot-enhance":
            cot_input = ctx.run_dir / "cot-input"
            cot_input.mkdir(parents=True, exist_ok=True)
            for index, asset in enumerate(valid_assets):
                source = ensure_within(Path(asset["path"]), settings.runtime_root)
                shutil.copy2(source, cot_input / f"{index + 1}-{source.name}")
            create_input = next(cot_input.iterdir()) if len(valid_assets) == 1 else cot_input
        else:
            parsed_candidates: list[Path] = []
            for index, asset in enumerate(valid_assets):
                source = prepare_source(
                    ensure_within(Path(asset["path"]), settings.runtime_root), ctx.run_dir
                )
                parsed_name = f"asset_{index + 1}_{asset['id'][:8]}"
                ingest_result = await run_command(
                    [
                        settings.synthetic_cli,
                        "--config",
                        config,
                        "ingest",
                        source,
                        "--output-dir",
                        ctx.run_dir / "parsed",
                        "--name",
                        parsed_name,
                    ],
                    cwd=ctx.run_dir,
                    ctx=ctx,
                    env=environment(),
                    timeout_seconds=180,
                )
                candidates = sorted((ctx.run_dir / "parsed").glob(f"{parsed_name}*"))
                if command_failed(ingest_result) or not candidates:
                    return ToolResult(
                        success=False,
                        summary=f"{asset['filename']} 解析失败",
                        error=ToolError(
                            code="synthetic_ingest_failed",
                            message="Ingest artifact missing",
                        ),
                    )
                parsed_candidates.append(candidates[-1])
            create_input = (
                parsed_candidates[0]
                if len(parsed_candidates) == 1
                else ctx.run_dir / "parsed"
            )

        await ctx.emit(
            "调用模型生成数据",
            "stage",
            {"stage_id": "generate"},
        )
        before = set((ctx.run_dir / "generated").glob("*.json"))
        create_result = await run_command(
            [
                settings.synthetic_cli,
                "--config",
                config,
                "create",
                create_input,
                "--type",
                params.content_type,
                "--num-pairs",
                str(params.num_pairs),
                "--chunk-size",
                str(params.chunk_size),
                "--chunk-overlap",
                str(params.chunk_overlap),
                "--output-dir",
                ctx.run_dir / "generated",
                "--model",
                model,
                "--verbose",
            ],
            cwd=ctx.run_dir,
            ctx=ctx,
            env=environment(),
            timeout_seconds=600,
        )
        generated_candidates = sorted(
            set((ctx.run_dir / "generated").glob("*.json")) - before,
            key=lambda path: path.stat().st_mtime,
        )
        if not generated_candidates:
            generated_candidates = sorted(
                (ctx.run_dir / "generated").glob("*.json"),
                key=lambda path: path.stat().st_mtime,
            )
        if command_failed(create_result) or not generated_candidates:
            return ToolResult(
                success=False,
                summary="CoT 生成阶段未产生有效 JSON",
                error=ToolError(code="synthetic_create_failed", message="Generated JSON missing"),
            )
        generated_path = generated_candidates[-1]
        cot_enhance_input_records: list[dict[str, str]] = []
        cot_enhance_normalized: Path | None = None
        if params.content_type == "cot-enhance":
            for asset in valid_assets:
                source = ensure_within(Path(asset["path"]), settings.runtime_root)
                try:
                    cot_enhance_input_records.extend(
                        cot_enhance_records(json.loads(source.read_text(encoding="utf-8")))
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    pass

            enhanced_records: list[dict[str, str]] = []
            for path in generated_candidates:
                try:
                    enhanced_records.extend(
                        cot_enhance_records(json.loads(path.read_text(encoding="utf-8")))
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
            if not enhanced_records:
                return ToolResult(
                    success=False,
                    summary="CoT 增强结果无法转换为问答样本",
                    error=ToolError(
                        code="cot_enhance_output_invalid",
                        message="模型返回了文件，但其中没有可读取的用户问题和助手回答",
                    ),
                )

            original_answers = {
                row["question"]: row["answer"] for row in cot_enhance_input_records
            }
            normalized_pairs = []
            for row in enhanced_records:
                enhanced_answer = row["answer"]
                normalized_pairs.append(
                    {
                        "question": row["question"],
                        "answer": original_answers.get(row["question"], enhanced_answer),
                        "reasoning": enhanced_answer,
                    }
                )
            cot_enhance_normalized = (
                ctx.run_dir / "generated" / "cot_enhance_qa_pairs.json"
            )
            cot_enhance_normalized.write_text(
                json.dumps(
                    {"qa_pairs": normalized_pairs},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        if params.content_type == "summary":
            records: list[dict[str, Any]] = []
            for path in generated_candidates:
                value = json.loads(path.read_text(encoding="utf-8"))
                records.append(
                    {
                        "question": f"请概括文档 {path.stem}",
                        "answer": value.get("summary", ""),
                        "reasoning": "",
                        "source": path.stem,
                    }
                )
            final_path = ctx.run_dir / "final" / "output.jsonl"
            final_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
                encoding="utf-8",
            )
            curated_paths = generated_candidates
        else:
            await ctx.emit(
                "使用 Judge 模型筛选质量",
                "stage",
                {"stage_id": "curate"},
            )
            curated_target: Path = (
                ctx.run_dir / "curated" / "curated.json"
                if len(generated_candidates) == 1 or cot_enhance_normalized
                else ctx.run_dir / "curated"
            )
            curate_input: Path = cot_enhance_normalized or (
                generated_candidates[0]
                if len(generated_candidates) == 1
                else ctx.run_dir / "generated"
            )
            curate_result = await run_command(
                [
                    settings.synthetic_cli,
                    "--config",
                    config,
                    "curate",
                    curate_input,
                    "--threshold",
                    str(params.threshold),
                    "--output",
                    curated_target,
                    "--model",
                    settings.llm_judge_model,
                    "--verbose",
                ],
                cwd=ctx.run_dir,
                ctx=ctx,
                env=environment(),
                timeout_seconds=900,
            )
            curated_paths = (
                [curated_target]
                if curated_target.is_file()
                else sorted(curated_target.glob("*.json"))
            )
            if command_failed(curate_result) or not curated_paths:
                return ToolResult(
                    success=False,
                    summary="质量筛选未产生有效 JSON",
                    error=ToolError(code="synthetic_curate_failed", message="Curated JSON missing"),
                )

            await ctx.emit(
                "整理统一输出格式",
                "stage",
                {"stage_id": "export"},
            )
            records = []
            for path in curated_paths:
                records.extend(parse_json_records(path))
            reasoning_sources = (
                [cot_enhance_normalized]
                if cot_enhance_normalized
                else generated_candidates
            )
            reasoning_by_question: dict[str, str] = {}
            for path in reasoning_sources:
                for source_row in parse_json_records(path):
                    question = source_row.get("question", source_row.get("prompt", ""))
                    reasoning = source_row.get("reasoning", source_row.get("cot", ""))
                    if isinstance(question, str) and isinstance(reasoning, str) and reasoning:
                        reasoning_by_question[question] = reasoning
            for row in records:
                question = row.get("question", row.get("prompt", ""))
                if (
                    isinstance(question, str)
                    and not row.get("reasoning")
                    and not row.get("cot")
                    and question in reasoning_by_question
                ):
                    row["reasoning"] = reasoning_by_question[question]
            final_path = ctx.run_dir / "final" / "output.jsonl"
            final_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in records)
                + ("\n" if records else ""),
                encoding="utf-8",
            )
        samples = []
        for index, row in enumerate(records):
            source_asset = valid_assets[min(index, len(valid_assets) - 1)]
            samples.append(
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": params.content_type,
                    "question": row.get("question", row.get("prompt", "")),
                    "answer": row.get("answer", row.get("response", "")),
                    "reasoning": row.get("reasoning", row.get("cot", "")),
                    "source": {
                        "file": source_asset["filename"],
                        "chunk_id": row.get("chunk_id", ""),
                        "evidence": row.get("source", row.get("context", "")),
                    },
                    "generation": {
                        "tool": "synthetic-data-kit",
                        "commit": commit,
                        "model": model,
                        "config": params.model_dump(),
                    },
                    "quality": {
                        "rule_passed": True,
                        "model_score": row.get("rating", row.get("score", 0)),
                        "human_status": "pending",
                    },
                }
            )
        count = len(samples)
        await ctx.emit(
            f"筛选后保留 {count} 条样本",
            "metric",
            {"generated_target": params.num_pairs, "retained": count},
        )
        return ToolResult(
            success=count >= params.min_retained,
            data={
                "samples": samples,
                "generated_path": str(generated_candidates[-1]),
                "curated_path": str(curated_paths[-1]),
                "final_path": str(final_path),
                "retained": count,
            },
            summary=f"Synthetic Data Kit 已筛选并导出 {count} 条 {params.content_type.upper()} 样本",
            error=(
                None
                if count >= params.min_retained
                else ToolError(
                    code="retained_below_minimum",
                    message=f"筛选后仅保留 {count} 条，低于要求的 {params.min_retained} 条",
                )
            ),
            persisted_path=str(final_path),
        )

    async def curate_export(
        params: SyntheticCurateInput, ctx: ExecutionContext
    ) -> ToolResult:
        source_run = database.get_run(params.source_run_id)
        if not source_run or source_run["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="找不到来源运行",
                error=ToolError(code="source_run_not_found", message="Source run not found"),
            )
        result_data = source_run.get("result") or {}
        source_path_value = (
            result_data.get("data", {}).get("curated_path")
            or source_run.get("artifact_path")
        )
        if not source_path_value:
            return ToolResult(
                success=False,
                summary="来源运行没有可导出的产物",
                error=ToolError(code="artifact_not_found", message="Artifact missing"),
            )
        source_path = ensure_within(Path(source_path_value), settings.runtime_root)
        extension = "jsonl" if params.format == "jsonl" else "json"
        output = ctx.run_dir / f"output_{params.format}.{extension}"
        config = ctx.run_dir / "config.yaml"
        write_config(config, ctx.run_dir, settings, settings.llm_model)
        result = await run_command(
            [
                settings.synthetic_cli,
                "--config",
                config,
                "save-as",
                source_path,
                "--format",
                params.format,
                "--output",
                output,
            ],
            cwd=ctx.run_dir,
            ctx=ctx,
            env=environment(),
            timeout_seconds=120,
        )
        if command_failed(result) or not output.exists():
            return ToolResult(
                success=False,
                summary="格式转换失败",
                error=ToolError(code="synthetic_export_failed", message="Output missing"),
            )
        return ToolResult(
            success=True,
            data={"final_path": str(output), "format": params.format},
            summary=f"已导出 {params.format.upper()} 格式",
            persisted_path=str(output),
        )

    return [
        build_tool(
            name="synthetic_ingest",
            description="调用 Synthetic Data Kit 原始 ingest 命令解析上传文档。",
            short_description="解析文档为 Synthetic Data Kit 中间数据",
            input_schema=SyntheticIngestInput,
            execute=ingest,
            tier=ToolTier.DOMAIN,
            version=commit[:7],
            tags=["document-synthesis", "data-processing", "dataset-construction"],
            hints=BehaviorHints(idempotent=True),
            timeout_ms=180_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.synthetic_cli.exists(),
        ),
        build_tool(
            name="synthetic_generate_cot",
            description="执行 system-check、ingest、CoT/QA 生成、质量筛选与 JSONL 导出完整链路。",
            short_description="生成、筛选并导出 CoT/QA",
            input_schema=SyntheticGenerateInput,
            execute=generate,
            tier=ToolTier.DOMAIN,
            version=commit[:7],
            tags=["cot-generation", "reasoning", "document-synthesis", "dataset-construction"],
            hints=BehaviorHints(open_world=True),
            timeout_ms=1_800_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.synthetic_cli.exists(),
        ),
        build_tool(
            name="synthetic_curate_export",
            description="把已有 Synthetic Data Kit 运行产物转换为 JSONL、Alpaca、FT 或 ChatML。",
            short_description="转换 Synthetic Data Kit 输出格式",
            input_schema=SyntheticCurateInput,
            execute=curate_export,
            tier=ToolTier.DOMAIN,
            version=commit[:7],
            tags=["cot-generation", "data-processing", "dataset-construction"],
            hints=BehaviorHints(idempotent=True),
            timeout_ms=120_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.synthetic_cli.exists(),
        ),
    ]
