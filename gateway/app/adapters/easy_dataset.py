from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
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
from ..security import convert_to_markdown, ensure_within


EVAL_TYPE_MAP = {
    "true-false": "true_false",
    "single-choice": "single_choice",
    "multiple-choice": "multiple_choice",
    "short-answer": "short_answer",
    "open": "open_ended",
}


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


class EasyDatasetIngestInput(BaseModel):
    asset_id: str
    project_name: str | None = None


class EasyDatasetGenerateInput(BaseModel):
    source_run_id: str
    target_count: int = Field(default=20, ge=1, le=100)
    questions_per_chunk: int = Field(default=10, ge=1, le=30)
    model: str | None = None
    language: str = "中文"
    ga_expansion: bool = False
    conversation_mode: Literal["single", "multi", "both"] = "single"


class EasyDatasetExportInput(BaseModel):
    source_run_id: str
    format: Literal["jsonl", "alpaca", "chatml"] = "jsonl"


class EasyDatasetWorkflowInput(BaseModel):
    workflow_type: Literal[
        "document-qa", "distillation", "image-qa", "evaluation"
    ] = "document-qa"
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    target_count: int = Field(default=20, ge=1, le=500)
    language: str = "中文"
    model: str | None = None
    conversation_mode: Literal["single", "multi", "both"] = "single"
    ga_expansion: bool = False
    split_mode: str = "smart"
    topic: str = ""
    tag_depth: int = Field(default=2, ge=1, le=5)
    tags_per_level: int = Field(default=3, ge=1, le=20)
    questions_per_tag: int = Field(default=3, ge=1, le=50)
    question_types: list[str] = Field(default_factory=list)
    questions_per_type: int = Field(default=3, ge=1, le=30)
    questions_per_image: int = Field(default=3, ge=1, le=30)
    vision_model: str | None = None


def _extract_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _model_reference(
    model_config: dict[str, Any], model: str, model_type: str
) -> dict[str, Any]:
    """Return the complete model object expected by Easy Dataset services.

    The API key in this object is always the non-secret loopback proxy token
    created by this adapter. The real provider credential never enters the
    sidecar.
    """
    return {
        "id": model_config.get("id"),
        "providerId": model_config.get("providerId", "custom"),
        "providerName": model_config.get("providerName", "本机安全代理"),
        "endpoint": model_config.get("endpoint"),
        "apiKey": model_config.get("apiKey"),
        "modelId": model_config.get("modelId", model),
        "modelName": model_config.get("modelName", model),
        "type": model_config.get("type", model_type),
        "temperature": model_config.get("temperature", 0.4),
        "maxTokens": model_config.get("maxTokens", 8192),
        "topK": model_config.get("topK", 0),
        "topP": model_config.get("topP", 0.9),
    }


def build_easy_dataset_tools(database: Database, settings: Settings) -> list:
    commit = git_commit(settings.easy_dataset_repo)
    timeout = httpx.Timeout(60, connect=5)

    async def request(
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        headers: dict[str, str] | None = None,
        timeout_override: float | None = None,
    ) -> Any:
        async with httpx.AsyncClient(
            base_url=settings.easy_dataset_base_url,
            timeout=timeout_override or timeout,
        ) as client:
            response = await client.request(
                method,
                path,
                json=json_body,
                content=content,
                files=files,
                headers=headers,
            )
        response.raise_for_status()
        return response.json()

    async def ensure_project(name: str) -> dict[str, Any]:
        projects = await request("GET", "/api/projects")
        existing = next(
            (
                item
                for item in _extract_list(projects, "data", "projects")
                if item.get("name") == name
            ),
            None,
        )
        if existing:
            return existing
        return await request(
            "POST",
            "/api/projects",
            json_body={
                "name": name,
                "description": "由本机合成数据工作台创建的 sidecar 项目",
            },
        )

    async def configure_model(project_id: str, model: str) -> dict[str, Any]:
        # Easy Dataset only stores a non-secret local proxy token. The real
        # credential remains in this gateway process environment.
        return await request(
            "POST",
            f"/api/projects/{project_id}/model-config",
            json_body={
                "providerId": "custom",
                "providerName": "本机安全代理",
                "endpoint": f"{settings.gateway_base_url}/internal/llm/v1",
                "apiKey": "local-gateway-proxy",
                "modelId": model,
                "modelName": model,
                "type": "text",
                "temperature": 0.4,
                "maxTokens": 8192,
                "topK": 0,
                "topP": 0.9,
                "status": 1,
            },
        )

    async def configure_vision_model(
        project_id: str, model: str
    ) -> dict[str, Any]:
        # As with the text model, Easy Dataset receives only a loopback proxy
        # endpoint and a non-secret placeholder token.
        return await request(
            "POST",
            f"/api/projects/{project_id}/model-config",
            json_body={
                "providerId": "custom",
                "providerName": "本机视觉安全代理",
                "endpoint": f"{settings.gateway_base_url}/internal/vision/v1",
                "apiKey": "local-gateway-vision-proxy",
                "modelId": model,
                "modelName": model,
                "type": "vision",
                "temperature": 0.2,
                "maxTokens": 4096,
                "topK": 0,
                "topP": 0.9,
                "status": 1,
            },
        )

    async def ingest(
        params: EasyDatasetIngestInput, ctx: ExecutionContext
    ) -> ToolResult:
        asset = database.get_asset(params.asset_id)
        if not asset or asset["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="找不到输入资产",
                error=ToolError(code="asset_not_found", message="Asset not found"),
            )
        try:
            await request("GET", "/api/projects")
        except Exception as exc:
            return ToolResult(
                success=False,
                summary="Easy Dataset sidecar 不可用",
                error=ToolError(
                    code="easy_dataset_unavailable",
                    message="请先在 127.0.0.1:1717 启动 Easy Dataset",
                    details={"type": type(exc).__name__},
                ),
            )

        source = ensure_within(Path(asset["path"]), settings.runtime_root)
        upload = source
        if source.suffix.lower() in {".txt", ".docx"}:
            upload = ctx.run_dir / f"{source.stem}.md"
            convert_to_markdown(source, upload)
        project_name = params.project_name or f"workbench-{ctx.project_id[:8]}"
        try:
            project = await ensure_project(project_name)
            easy_project_id = str(project["id"])
            upload_result = await request(
                "POST",
                f"/api/projects/{easy_project_id}/files",
                content=upload.read_bytes(),
                headers={
                    "content-type": "application/octet-stream",
                    "x-file-name": quote(upload.name),
                },
                timeout_override=180,
            )
            file_id = str(upload_result["fileId"])
            split_result = await request(
                "POST",
                f"/api/projects/{easy_project_id}/split",
                json_body={
                    "fileNames": [{"fileName": upload.name, "fileId": file_id}],
                    "model": {"providerId": "local", "modelName": "split-only"},
                    "language": "中文",
                    "domainTreeAction": "keep",
                },
                timeout_override=180,
            )
            # `/split` returns display-oriented chunk objects without the
            # persisted database IDs required by `/chunks/{chunkId}/questions`.
            # Resolve the saved rows explicitly from the file ID before
            # exposing the manifest to later workflow stages.
            persisted_chunks = await request(
                "POST",
                f"/api/projects/{easy_project_id}/chunks",
                json_body={"array": [file_id]},
                timeout_override=180,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                summary="Easy Dataset 导入或分块失败",
                error=ToolError(
                    code="easy_dataset_ingest_failed",
                    message=f"HTTP {exc.response.status_code}",
                    details={"body": exc.response.text[:1000]},
                ),
            )
        chunks = _extract_list(persisted_chunks, "data", "chunks")
        if not chunks:
            chunks = _extract_list(split_result, "chunks")
        manifest = {
            "easy_project_id": easy_project_id,
            "easy_project_name": project_name,
            "file_id": file_id,
            "file_name": upload.name,
            "chunks": chunks,
            "chunk_count": int(split_result.get("totalChunks", len(chunks))),
        }
        manifest_path = ctx.run_dir / "easy-ingest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ToolResult(
            success=bool(chunks),
            data=manifest,
            summary=f"Easy Dataset 已导入并切分为 {len(chunks)} 个文本块",
            error=(
                None
                if chunks
                else ToolError(
                    code="easy_dataset_no_chunks", message="Split produced no chunks"
                )
            ),
            persisted_path=str(manifest_path),
        )

    async def generate(
        params: EasyDatasetGenerateInput, ctx: ExecutionContext
    ) -> ToolResult:
        if not settings.llm_ready:
            return ToolResult(
                success=False,
                summary="LLM 凭据尚未完成轮换确认",
                error=ToolError(
                    code="llm_credential_not_ready",
                    message=(
                        "请撤销旧密钥，设置新的 DEEPSEEK_API_KEY，"
                        "并显式设置 LLM_CREDENTIAL_ROTATED=true"
                    ),
                ),
            )
        source_run = database.get_run(params.source_run_id)
        if not source_run or source_run["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="找不到 Easy Dataset 导入运行",
                error=ToolError(code="source_run_not_found", message="Source run not found"),
            )
        source_data = (source_run.get("result") or {}).get("data") or {}
        easy_project_id = source_data.get("easy_project_id")
        chunks = source_data.get("chunks") or []
        if not easy_project_id or not chunks:
            return ToolResult(
                success=False,
                summary="来源运行缺少 Easy Dataset 文本块",
                error=ToolError(code="source_run_invalid", message="Missing sidecar metadata"),
            )
        model = params.model or settings.llm_model
        try:
            model_config = await configure_model(easy_project_id, model)
            model_ref = _model_reference(model_config, model, "text")
            produced = 0
            for chunk in chunks:
                if produced >= params.target_count:
                    break
                chunk_id = str(chunk.get("id"))
                remaining = params.target_count - produced
                result = await request(
                    "POST",
                    f"/api/projects/{easy_project_id}/chunks/{chunk_id}/questions",
                    json_body={
                        "model": model_ref,
                        "language": params.language,
                        "number": min(params.questions_per_chunk, remaining),
                        "enableGaExpansion": params.ga_expansion,
                    },
                    timeout_override=600,
                )
                produced += int(result.get("total", 0))
                await ctx.emit(
                    f"文本块 {chunk_id} 已累计生成 {produced} 个问题", "metric"
                )
            questions_payload = await request(
                "GET", f"/api/projects/{easy_project_id}/questions?all=true"
            )
            questions = _extract_list(questions_payload, "data", "questions")
            selected = questions[: params.target_count]
            generated_rows = []
            for index, question in enumerate(selected):
                if params.conversation_mode in {"single", "both"}:
                    dataset = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/datasets",
                        json_body={
                            "questionId": question["id"],
                            "model": model_ref,
                            "language": params.language,
                        },
                        timeout_override=600,
                    )
                    generated_rows.append(dataset)
                if params.conversation_mode in {"multi", "both"}:
                    conversation = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/dataset-conversations",
                        json_body={
                            "questionId": question["id"],
                            "rounds": 3,
                            "roleA": "用户",
                            "roleB": "助手",
                            "model": {
                                **model_ref,
                                "modelId": model,
                            },
                            "language": params.language,
                        },
                        timeout_override=600,
                    )
                    generated_rows.append(
                        {
                            "question": question.get("question", ""),
                            "answer": json.dumps(
                                conversation.get("data", {}),
                                ensure_ascii=False,
                            ),
                            "cot": "",
                            "conversation": True,
                        }
                    )
                await ctx.emit(
                    f"已生成答案 {index + 1}/{len(selected)}", "metric"
                )
            exported = await request(
                "POST",
                f"/api/projects/{easy_project_id}/datasets/export",
                json_body={"batchMode": False},
                timeout_override=180,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                summary="Easy Dataset 问答生成失败",
                error=ToolError(
                    code="easy_dataset_generate_failed",
                    message=f"HTTP {exc.response.status_code}",
                    details={"body": exc.response.text[:1000]},
                ),
            )
        rows = _extract_list(exported, "data", "datasets") or generated_rows
        samples: list[dict[str, Any]] = []
        for index, row in enumerate(rows[: params.target_count]):
            question_value = row.get("question", "")
            if isinstance(question_value, dict):
                question_value = question_value.get("question", "")
            samples.append(
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": "document_qa",
                    "question": question_value,
                    "answer": row.get("answer", ""),
                    "reasoning": row.get("cot", ""),
                    "source": {
                        "file": source_data.get("file_name", ""),
                        "chunk_id": str(row.get("chunkId", "")),
                        "evidence": row.get("chunkContent", ""),
                    },
                    "generation": {
                        "tool": "easy-dataset",
                        "commit": commit,
                        "model": model,
                        "sidecar_project_id": easy_project_id,
                    },
                    "quality": {
                        "rule_passed": bool(row.get("answer")),
                        "model_score": row.get("score", 0) or 0,
                        "human_status": "pending",
                    },
                }
            )
        raw_path = ctx.run_dir / "easy-dataset-export.json"
        raw_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ToolResult(
            success=len(samples) >= params.target_count,
            data={
                "samples": samples,
                "easy_project_id": easy_project_id,
                "raw_path": str(raw_path),
                "generated": len(samples),
            },
            summary=f"Easy Dataset 已生成 {len(samples)} 条问答数据",
            error=(
                None
                if len(samples) >= params.target_count
                else ToolError(
                    code="easy_dataset_below_target",
                    message=f"仅生成 {len(samples)} 条，目标为 {params.target_count} 条",
                )
            ),
            persisted_path=str(raw_path),
        )

    async def workflow(
        params: EasyDatasetWorkflowInput, ctx: ExecutionContext
    ) -> ToolResult:
        if params.workflow_type == "image-qa" and not settings.vision_ready:
            return ToolResult(
                success=False,
                summary="视觉模型尚未配置",
                error=ToolError(
                    code="vision_not_configured",
                    message="图片已可上传，但自动问答需要配置独立视觉模型",
                ),
            )
        if not settings.llm_ready and params.workflow_type != "image-qa":
            return ToolResult(
                success=False,
                summary="文本模型密钥尚未完成轮换确认",
                error=ToolError(
                    code="llm_credential_not_ready",
                    message=(
                        "请设置新的 DEEPSEEK_API_KEY，"
                        "并显式设置 LLM_CREDENTIAL_ROTATED=true"
                    ),
                ),
            )

        await ctx.emit(
            "检查 Easy Dataset 服务与模型配置",
            "stage",
            {"stage_id": "check"},
        )
        try:
            await request("GET", "/api/projects")
        except Exception as exc:
            return ToolResult(
                success=False,
                summary="Easy Dataset 服务不可达",
                error=ToolError(
                    code="easy_dataset_unavailable",
                    message="请确认 127.0.0.1:1717 已启动",
                    details={"type": type(exc).__name__},
                ),
            )

        if params.workflow_type == "document-qa":
            if not params.asset_ids:
                return ToolResult(
                    success=False,
                    summary="请先上传文档",
                    error=ToolError(code="asset_required", message="Document asset required"),
                )
            combined_samples: list[dict[str, Any]] = []
            last_path: str | None = None
            per_asset = max(1, params.target_count // len(params.asset_ids))
            for index, asset_id in enumerate(params.asset_ids):
                await ctx.emit(
                    f"上传并解析第 {index + 1}/{len(params.asset_ids)} 份文档",
                    "stage",
                    {"stage_id": "ingest"},
                )
                ingest_result = await ingest(
                    EasyDatasetIngestInput(
                        asset_id=asset_id,
                        project_name=f"workbench-{ctx.project_id[:8]}",
                    ),
                    ctx,
                )
                if not ingest_result.success:
                    return ingest_result
                database.update_run(
                    ctx.run_id,
                    result_json={"data": ingest_result.data},
                )
                await ctx.emit(
                    "生成问题、答案与思维链",
                    "stage",
                    {"stage_id": "generate"},
                )
                generated = await generate(
                    EasyDatasetGenerateInput(
                        source_run_id=ctx.run_id,
                        target_count=(
                            params.target_count - len(combined_samples)
                            if index == len(params.asset_ids) - 1
                            else per_asset
                        ),
                        model=params.model,
                        language=params.language,
                        ga_expansion=params.ga_expansion,
                        conversation_mode=params.conversation_mode,
                    ),
                    ctx,
                )
                if not generated.success and not (generated.data or {}).get("samples"):
                    return generated
                combined_samples.extend((generated.data or {}).get("samples", []))
                last_path = generated.persisted_path
            return ToolResult(
                success=len(combined_samples) >= params.target_count,
                data={
                    "samples": combined_samples[: params.target_count],
                    "generated": min(len(combined_samples), params.target_count),
                },
                summary=f"Easy Dataset 已生成 {min(len(combined_samples), params.target_count)} 条文档数据",
                error=(
                    None
                    if len(combined_samples) >= params.target_count
                    else ToolError(
                        code="easy_dataset_below_target",
                        message="实际生成量低于目标数量",
                    )
                ),
                persisted_path=last_path,
            )

        if params.workflow_type == "image-qa":
            if not params.asset_ids:
                return ToolResult(
                    success=False,
                    summary="图片问答需要图片、PDF 或 ZIP 输入",
                    error=ToolError(
                        code="asset_required",
                        message="Image, PDF, or ZIP asset required",
                    ),
                )
            project = await ensure_project(f"workbench-{ctx.project_id[:8]}")
            easy_project_id = str(project["id"])
            model = params.vision_model or settings.vision_model
            model_config = await configure_vision_model(easy_project_id, model)
            model_ref = _model_reference(model_config, model, "vision")
            imported_images: list[dict[str, Any]] = []
            await ctx.emit(
                "导入图片并建立缩略图索引",
                "stage",
                {"stage_id": "ingest"},
            )
            for asset_id in params.asset_ids:
                asset = database.get_asset(asset_id)
                if not asset or asset["project_id"] != ctx.project_id:
                    return ToolResult(
                        success=False,
                        summary="找不到图片输入",
                        error=ToolError(
                            code="asset_not_found",
                            message=f"Asset {asset_id} not found",
                        ),
                    )
                source = ensure_within(Path(asset["path"]), settings.runtime_root)
                suffix = source.suffix.lower()
                if suffix == ".pdf":
                    imported = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/images/pdf-convert",
                        files={
                            "file": (
                                source.name,
                                source.read_bytes(),
                                "application/pdf",
                            )
                        },
                        timeout_override=600,
                    )
                else:
                    if suffix == ".zip":
                        zip_name = source.name
                        zip_bytes = source.read_bytes()
                    else:
                        buffer = io.BytesIO()
                        with zipfile.ZipFile(
                            buffer, "w", compression=zipfile.ZIP_DEFLATED
                        ) as archive:
                            archive.writestr(source.name, source.read_bytes())
                        zip_name = f"{source.stem}.zip"
                        zip_bytes = buffer.getvalue()
                    imported = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/images/zip-import",
                        files={
                            "file": (
                                zip_name,
                                zip_bytes,
                                "application/zip",
                            )
                        },
                        timeout_override=600,
                    )
                imported_images.extend(_extract_list(imported, "images"))

            await ctx.emit(
                "生成图片问题与答案",
                "stage",
                {"stage_id": "generate"},
            )
            generated_rows: list[dict[str, Any]] = []
            for image in imported_images:
                image_name = str(
                    image.get("imageName") or image.get("name") or ""
                )
                if not image_name:
                    continue
                question_payload = await request(
                    "POST",
                    f"/api/projects/{easy_project_id}/images/questions",
                    json_body={
                        "imageName": image_name,
                        "count": params.questions_per_image,
                        "model": model_ref,
                        "language": params.language,
                    },
                    timeout_override=600,
                )
                for question_index, question in enumerate(
                    question_payload.get("questions", [])
                ):
                    question_text = (
                        question.get("question", "")
                        if isinstance(question, dict)
                        else str(question)
                    )
                    if not question_text:
                        continue
                    dataset = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/images/datasets",
                        json_body={
                            "imageName": image_name,
                            "question": {
                                "id": (
                                    question.get("id")
                                    if isinstance(question, dict)
                                    else None
                                ),
                                "question": question_text,
                            },
                            "model": model_ref,
                            "language": params.language,
                        },
                        timeout_override=600,
                    )
                    generated_rows.append(
                        {
                            "image_name": image_name,
                            "question_index": question_index,
                            "question": question_text,
                            "answer": dataset.get("answer", ""),
                            "dataset": dataset.get("dataset"),
                        }
                    )
                    if len(generated_rows) >= params.target_count:
                        break
                if len(generated_rows) >= params.target_count:
                    break
            samples = [
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": "image_qa",
                    "question": row["question"],
                    "answer": row["answer"],
                    "reasoning": "",
                    "source": {
                        "file": row["image_name"],
                        "chunk_id": "",
                        "evidence": "Easy Dataset 图片输入",
                    },
                    "generation": {
                        "tool": "easy-dataset",
                        "commit": commit,
                        "model": model,
                    },
                    "quality": {
                        "rule_passed": bool(row["answer"]),
                        "model_score": 0,
                        "human_status": "pending",
                    },
                }
                for index, row in enumerate(generated_rows)
            ]
            artifact = ctx.run_dir / "image-qa.json"
            artifact.write_text(
                json.dumps(
                    {
                        "images": imported_images,
                        "rows": generated_rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return ToolResult(
                success=bool(samples),
                data={
                    "samples": samples,
                    "images": imported_images,
                    "generated": len(samples),
                },
                summary=f"图片问答生成 {len(samples)} 条样本",
                error=(
                    None
                    if samples
                    else ToolError(
                        code="vision_empty_result",
                        message="视觉模型未返回可保存的问答",
                    )
                ),
                persisted_path=str(artifact),
            )

        model = params.model or settings.llm_model
        project = await ensure_project(f"workbench-{ctx.project_id[:8]}")
        easy_project_id = str(project["id"])
        model_config = await configure_model(easy_project_id, model)
        model_ref = _model_reference(model_config, model, "text")

        if params.workflow_type == "distillation":
            if not params.topic.strip():
                return ToolResult(
                    success=False,
                    summary="请输入蒸馏主题",
                    error=ToolError(code="topic_required", message="Topic is required"),
                )
            await ctx.emit(
                "生成标签树",
                "stage",
                {"stage_id": "tags"},
            )
            current = [
                {
                    "label": params.topic.strip(),
                    "id": None,
                    "path": params.topic.strip(),
                }
            ]
            tree: list[dict[str, Any]] = []
            for _depth in range(params.tag_depth):
                next_level: list[dict[str, Any]] = []
                for parent in current:
                    tags = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/distill/tags",
                        json_body={
                            "parentTag": parent["label"],
                            "parentTagId": parent["id"],
                            "tagPath": parent["path"],
                            "count": params.tags_per_level,
                            "model": model_ref,
                            "language": params.language,
                        },
                        timeout_override=600,
                    )
                    for tag in _extract_list(tags):
                        node = {
                            "id": tag.get("id"),
                            "label": tag.get("label", ""),
                            "parent_id": parent["id"],
                            "path": f"{parent['path']} / {tag.get('label', '')}",
                        }
                        tree.append(node)
                        next_level.append(node)
                current = next_level
            await ctx.emit(
                "按叶子标签生成问题与答案",
                "stage",
                {"stage_id": "generate"},
            )
            generated_rows: list[dict[str, Any]] = []
            for leaf in current:
                questions = await request(
                    "POST",
                    f"/api/projects/{easy_project_id}/distill/questions",
                    json_body={
                        "tagPath": leaf["path"],
                        "currentTag": leaf["label"],
                        "tagId": leaf["id"],
                        "count": params.questions_per_tag,
                        "model": model_ref,
                        "language": params.language,
                    },
                    timeout_override=600,
                )
                for question in _extract_list(questions):
                    dataset = await request(
                        "POST",
                        f"/api/projects/{easy_project_id}/datasets",
                        json_body={
                            "questionId": question["id"],
                            "model": model_ref,
                            "language": params.language,
                        },
                        timeout_override=600,
                    )
                    generated_rows.append(dataset)
                    if len(generated_rows) >= params.target_count:
                        break
                if len(generated_rows) >= params.target_count:
                    break
            samples = []
            for index, row in enumerate(generated_rows):
                question = row.get("question", "")
                if isinstance(question, dict):
                    question = question.get("question", "")
                samples.append(
                    {
                        "id": f"{ctx.run_id}/{index + 1}",
                        "task_type": "distillation",
                        "question": question,
                        "answer": row.get("answer", ""),
                        "reasoning": row.get("cot", ""),
                        "source": {
                            "file": "主题蒸馏",
                            "chunk_id": "",
                            "evidence": params.topic,
                            "tag_tree": tree,
                        },
                        "generation": {
                            "tool": "easy-dataset",
                            "commit": commit,
                            "model": model,
                        },
                        "quality": {
                            "rule_passed": bool(row.get("answer")),
                            "model_score": row.get("score", 0) or 0,
                            "human_status": "pending",
                        },
                    }
                )
            artifact = ctx.run_dir / "distillation.json"
            artifact.write_text(
                json.dumps({"tree": tree, "rows": generated_rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return ToolResult(
                success=bool(samples),
                data={"samples": samples, "tag_tree": tree, "generated": len(samples)},
                summary=f"数据蒸馏生成 {len(samples)} 条样本",
                persisted_path=str(artifact),
            )

        if params.workflow_type == "evaluation":
            if not params.asset_ids:
                return ToolResult(
                    success=False,
                    summary="评估数据需要文档输入",
                    error=ToolError(code="asset_required", message="Document asset required"),
                )
            await ctx.emit(
                "导入文档并预览文本块",
                "stage",
                {"stage_id": "ingest"},
            )
            ingested = await ingest(
                EasyDatasetIngestInput(
                    asset_id=params.asset_ids[0],
                    project_name=f"workbench-{ctx.project_id[:8]}",
                ),
                ctx,
            )
            if not ingested.success:
                return ingested
            manifest = ingested.data or {}
            await ctx.emit(
                "生成并筛选评估题",
                "stage",
                {"stage_id": "generate"},
            )
            selected_types = [
                EVAL_TYPE_MAP[item]
                for item in params.question_types
                if item in EVAL_TYPE_MAP
            ]
            if not selected_types:
                return ToolResult(
                    success=False,
                    summary="请至少选择一种评估题型",
                    error=ToolError(
                        code="question_type_required",
                        message="At least one evaluation question type is required",
                    ),
                )
            all_type_ratios = {
                "true_false": 0,
                "single_choice": 0,
                "multiple_choice": 0,
                "short_answer": 0,
                "open_ended": 0,
            }
            original_task_config = await request(
                "GET",
                f"/api/projects/{manifest['easy_project_id']}/tasks",
            )
            rows: list[dict[str, Any]] = []
            try:
                for question_type in selected_types:
                    type_rows: list[dict[str, Any]] = []
                    for chunk in manifest.get("chunks", []):
                        remaining = params.questions_per_type - len(type_rows)
                        if remaining <= 0:
                            break
                        content = str(chunk.get("content", ""))
                        question_length = max(1, len(content) // remaining)
                        ratios = {**all_type_ratios, question_type: 1}
                        await request(
                            "PUT",
                            f"/api/projects/{manifest['easy_project_id']}/tasks",
                            json_body={
                                **original_task_config,
                                "questionGenerationLength": question_length,
                                "evalQuestionTypeRatios": ratios,
                            },
                        )
                        generated = await request(
                            "POST",
                            (
                                f"/api/projects/{manifest['easy_project_id']}"
                                f"/chunks/{chunk['id']}/eval-questions"
                            ),
                            json_body={"model": model_ref, "language": params.language},
                            timeout_override=600,
                        )
                        candidates = _extract_list(
                            generated, "questions", "data", "items"
                        )
                        type_rows.extend(
                            row
                            for row in candidates
                            if row.get("questionType") == question_type
                        )
                    rows.extend(type_rows[: params.questions_per_type])
            finally:
                await request(
                    "PUT",
                    f"/api/projects/{manifest['easy_project_id']}/tasks",
                    json_body=original_task_config,
                )
            samples = [
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": "evaluation",
                    "question": row.get("question", ""),
                    "answer": str(row.get("correctAnswer", "")),
                    "reasoning": "",
                    "source": {
                        "file": manifest.get("file_name", ""),
                        "chunk_id": str(row.get("chunkId", "")),
                        "evidence": "",
                    },
                    "generation": {
                        "tool": "easy-dataset",
                        "commit": commit,
                        "model": model,
                        "question_type": row.get("questionType", ""),
                    },
                    "quality": {
                        "rule_passed": bool(row.get("correctAnswer")),
                        "model_score": row.get("score", 0) or 0,
                        "human_status": "pending",
                    },
                }
                for index, row in enumerate(rows[: params.target_count])
            ]
            artifact = ctx.run_dir / "evaluation.json"
            artifact.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            return ToolResult(
                success=len(samples) >= params.target_count,
                data={"samples": samples, "generated": len(samples)},
                summary=f"已生成 {len(samples)} 条评估数据",
                error=(
                    None
                    if len(samples) >= params.target_count
                    else ToolError(
                        code="easy_dataset_evaluation_below_target",
                        message=(
                            f"实际生成 {len(samples)} 条，"
                            f"低于所选题型目标 {params.target_count} 条"
                        ),
                    )
                ),
                persisted_path=str(artifact),
            )

        return ToolResult(
            success=False,
            summary="不支持的 Easy Dataset 工作流",
            error=ToolError(
                code="unsupported_workflow",
                message=params.workflow_type,
            ),
        )

    async def export(
        params: EasyDatasetExportInput, ctx: ExecutionContext
    ) -> ToolResult:
        source_run = database.get_run(params.source_run_id)
        if not source_run or source_run["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="找不到来源运行",
                error=ToolError(code="source_run_not_found", message="Source run not found"),
            )
        samples = database.list_samples(params.source_run_id)
        output = ctx.run_dir / f"easy-dataset.{params.format}"
        if params.format == "jsonl":
            lines = [json.dumps(item, ensure_ascii=False) for item in samples]
        elif params.format == "alpaca":
            lines = [
                json.dumps(
                    {
                        "instruction": item["question"],
                        "input": item["source"].get("evidence", ""),
                        "output": item["answer"],
                    },
                    ensure_ascii=False,
                )
                for item in samples
            ]
        else:
            lines = [
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": item["question"]},
                            {
                                "role": "assistant",
                                "content": item["answer"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
                for item in samples
            ]
        output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return ToolResult(
            success=bool(samples),
            data={"final_path": str(output), "format": params.format, "count": len(samples)},
            summary=f"已导出 {len(samples)} 条 {params.format.upper()} 数据",
            persisted_path=str(output),
        )

    return [
        build_tool(
            name="easy_dataset_ingest",
            description="通过真实 HTTP API 在未修改的 Easy Dataset sidecar 中创建项目、上传文档并分块。",
            short_description="上传并分块文档",
            input_schema=EasyDatasetIngestInput,
            execute=ingest,
            tier=ToolTier.EXTERNAL,
            version=commit[:7],
            tags=["document-synthesis", "data-processing", "dataset-construction"],
            hints=BehaviorHints(open_world=False),
            timeout_ms=360_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.easy_dataset_repo.exists(),
        ),
        build_tool(
            name="easy_dataset_generate",
            description="通过 Easy Dataset 后端 API 生成问题、答案与 CoT，并经本机安全代理访问模型。",
            short_description="生成文档问答与 CoT",
            input_schema=EasyDatasetGenerateInput,
            execute=generate,
            tier=ToolTier.EXTERNAL,
            version=commit[:7],
            tags=["document-synthesis", "cot-generation", "dataset-construction"],
            hints=BehaviorHints(open_world=True),
            timeout_ms=1_800_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.easy_dataset_repo.exists(),
        ),
        build_tool(
            name="easy_dataset_export",
            description="把 Easy Dataset 生成运行的统一样本导出为 JSONL、Alpaca 或 ChatML。",
            short_description="导出 Easy Dataset 样本",
            input_schema=EasyDatasetExportInput,
            execute=export,
            tier=ToolTier.EXTERNAL,
            version=commit[:7],
            tags=["document-synthesis", "data-processing", "dataset-construction"],
            hints=BehaviorHints(idempotent=True),
            timeout_ms=120_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.easy_dataset_repo.exists(),
        ),
        build_tool(
            name="easy_dataset_workflow",
            description="Easy Dataset 页面级工作流，编排文档问答、蒸馏、图片问答和评估数据。",
            short_description="执行 Easy Dataset 页面工作流",
            input_schema=EasyDatasetWorkflowInput,
            execute=workflow,
            tier=ToolTier.EXTERNAL,
            version=commit[:7],
            tags=["document-synthesis", "dataset-construction"],
            hints=BehaviorHints(open_world=True),
            timeout_ms=3_600_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.easy_dataset_repo.exists(),
        ),
    ]
