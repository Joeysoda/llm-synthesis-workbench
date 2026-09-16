from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
import csv
import io
import importlib.metadata
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .adapters.easy_dataset import build_easy_dataset_tools
from .adapters.cleanlab import build_cleanlab_tools
from .adapters.kaqg import build_kaqg_tools
from .adapters.synlogic import build_synlogic_tools
from .adapters.synthetic import build_synthetic_tools, cot_enhance_records
from .config import settings
from .contracts import RiskLevel, SafetyPolicy
from .database import Database, now_iso
from .medical import MedicalRunManager, medical_output_catalog, resolve_output_policy
from .pipeline import OPERATORS, templates, validate_pipeline
from .registry import ToolRegistry
from .runner import RunManager
from .schemas import (
    EasyDatasetJobCreate,
    EasyDatasetPreviewRequest,
    CleanlabDecisionRequest,
    CleanlabJobCreate,
    ExportRequest,
    ProbeRequest,
    KaqgJobCreate,
    ProjectCreate,
    RunCreate,
    SamplePatch,
    SynLogicJobCreate,
    SynLogicVerifyRequest,
    SyntheticJobCreate,
    ToolProjectCreate,
    ToolProjectPatch,
    MedicalGenerateRequest,
    MedicalGenerationCreate,
    PipelineCreate,
    PipelinePatch,
    PipelineRunCreate,
)
from .security import (
    ensure_within,
    sanitize_filename,
    sha256_bytes,
)

app = FastAPI(
    title="LLM 合成数据工作台网关",
    version="0.3.0",
    description="本机 Agent OS 兼容网关，只绑定 127.0.0.1。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

database = Database(settings.database_path)
registry = ToolRegistry()
registry.register_many(build_synthetic_tools(database, settings))
registry.register_many(build_easy_dataset_tools(database, settings))
registry.register_many(build_synlogic_tools(database, settings))
registry.register_many(build_kaqg_tools(database, settings))
registry.register_many(build_cleanlab_tools(database, settings))
manager = RunManager(database, registry, settings)
medical_manager = MedicalRunManager(database, settings)

TOOL_IDS = {
    "synthetic-data-kit", "easy-dataset", "synlogic", "kaqg", "cleanlab", "medical", "legacy"
}
TOOL_NAMES = {
    "synthetic-data-kit": "Synthetic Data Kit",
    "easy-dataset": "Easy Dataset",
    "synlogic": "SynLogic",
    "kaqg": "KAQG",
    "cleanlab": "Cleanlab",
    "medical": "医疗数据",
    "legacy": "历史实验",
}
PROBE_RESULTS: dict[str, dict[str, Any]] = {}


def require_project(project_id: str) -> dict[str, Any]:
    project = database.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def require_run(run_id: str) -> dict[str, Any]:
    run = database.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


def require_tool_project(project_id: str, tool_id: str) -> dict[str, Any]:
    project = require_project(project_id)
    if project.get("tool_id") != tool_id:
        raise HTTPException(
            status_code=409,
            detail=f"该项目属于 {TOOL_NAMES.get(project.get('tool_id'), '其他工具')}，不能运行当前工具",
        )
    if project.get("archived_at"):
        raise HTTPException(status_code=409, detail="归档项目为只读状态")
    return project


def validate_project_assets(project_id: str, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        asset = database.get_asset(asset_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=422, detail=f"资产 {asset_id} 不属于当前项目")


def validate_cot_enhance_assets(project_id: str, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        asset = database.get_asset(asset_id)
        if not asset or asset["project_id"] != project_id:
            raise HTTPException(status_code=422, detail=f"资产 {asset_id} 不属于当前项目")
        if Path(asset["filename"]).suffix.lower() != ".json":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{asset['filename']} 不是 JSON 文件。"
                    "“补充 CoT”用于已有问答数据；普通 DOCX/PDF 请改选“CoT 思维链”。"
                ),
            )
        try:
            path = ensure_within(Path(asset["path"]), settings.runtime_root)
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{asset['filename']} 不是可读取的 UTF-8 JSON 文件",
            ) from exc
        if not cot_enhance_records(value):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{asset['filename']} 中没有可增强的问答。"
                    '请使用 {"qa_pairs":[{"question":"...","answer":"..."}]} '
                    "或包含用户和助手消息的 conversations。"
                ),
            )


def stage_rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"id": stage_id, "label": label, "status": "pending"} for stage_id, label in pairs]


def git_version(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "llm-synthesis-workbench",
        "database": str(settings.database_path),
        "local_only": True,
    }


@app.get("/api/v1/integrations")
async def integrations() -> list[dict[str, Any]]:
    easy_ok = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            easy_ok = (await client.get(f"{settings.easy_dataset_base_url}/api/projects")).is_success
    except Exception:
        pass
    return [
        {
            "id": "synthetic-data-kit",
            "name": "Synthetic Data Kit",
            "status": "ready" if settings.synthetic_cli.exists() else "missing",
            "mode": "CLI",
            "model": settings.llm_model,
            "llm_ready": settings.llm_ready,
        },
        {
            "id": "easy-dataset",
            "name": "Easy Dataset",
            "status": "ready" if easy_ok else "offline",
            "mode": "HTTP sidecar",
            "endpoint": settings.easy_dataset_base_url,
            "llm_ready": settings.llm_ready,
        },
        {
            "id": "synlogic",
            "name": "SynLogic Arrow Maze",
            "status": "ready" if settings.synlogic_repo.exists() else "missing",
            "mode": "Python verifier",
            "model": "deterministic",
            "llm_ready": True,
        },
        {
            "id": "kaqg",
            "name": "KAQG",
            "status": "ready" if settings.kaqg_repo.exists() else "missing",
            "mode": "Neo4j + MQTT worker",
            "model": settings.llm_model,
            "llm_ready": settings.llm_ready,
        },
        {
            "id": "cleanlab",
            "name": "Cleanlab",
            "status": "ready",
            "mode": "local Python",
            "model": "TF-IDF + LogisticRegression",
            "llm_ready": True,
        },
    ]


@app.get("/api/v1/tools")
async def tools() -> list[dict[str, Any]]:
    pool = await registry.assemble_tool_pool(
        safety=SafetyPolicy(max_allowed_risk=RiskLevel.MEDIUM)
    )
    return [
        item
        for item in registry.generate_tool_descriptions(pool)
        if item["name"] != "easy_dataset_workflow"
    ]


@app.get("/api/v1/projects")
async def list_projects() -> list[dict[str, Any]]:
    return database.list_projects()


@app.post("/api/v1/projects", status_code=201)
async def create_project(payload: ProjectCreate) -> dict[str, Any]:
    try:
        return database.create_project(payload.name, payload.description)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="项目名称已存在") from exc


@app.get("/api/v1/projects/{project_id}/assets")
async def list_assets(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    return database.list_assets(project_id)


@app.post("/api/v1/projects/{project_id}/assets", status_code=201)
async def upload_asset(
    project_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    require_project(project_id)
    filename = sanitize_filename(file.filename or "")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="单文件不能超过 25 MB")
    asset_id = str(uuid.uuid4())
    upload_dir = settings.runtime_root / "uploads" / project_id / asset_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = ensure_within(upload_dir / filename, settings.runtime_root)
    path.write_bytes(content)
    return database.create_asset(
        {
            "id": asset_id,
            "project_id": project_id,
            "filename": filename,
            "path": str(path),
            "content_type": file.content_type or "application/octet-stream",
            "size": len(content),
            "sha256": sha256_bytes(content),
            "created_at": now_iso(),
        }
    )


@app.get("/api/v1/runs")
async def list_runs(
    project_id: str | None = Query(default=None), limit: int = Query(default=50, le=200)
) -> list[dict[str, Any]]:
    return database.list_runs(project_id, limit)


@app.post("/api/v1/runs", status_code=202)
async def create_run(payload: RunCreate) -> dict[str, Any]:
    require_project(payload.project_id)
    tool = registry.get(payload.tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    if tool.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
        if not payload.confirmed:
            raise HTTPException(status_code=409, detail="中风险工具需要参数预检确认")
    try:
        tool.input_schema.model_validate(payload.input)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = database.create_run(payload.project_id, payload.tool_name, payload.input)
    manager.start(run["id"])
    return run


@app.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return require_run(run_id)


@app.post("/api/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    require_run(run_id)
    return {"cancelled": await manager.cancel(run_id)}


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    require_run(run_id)

    async def stream():
        cursor = 0
        while True:
            if await request.is_disconnected():
                break
            events = database.list_events(run_id, cursor)
            for event in events:
                cursor = int(event["id"])
                yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            run = database.get_run(run_id)
            if run and run["status"] in {"succeeded", "failed", "cancelled"} and not events:
                yield f"event: end\ndata: {json.dumps({'status': run['status']})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/runs/{run_id}/samples")
async def run_samples(run_id: str) -> list[dict[str, Any]]:
    require_run(run_id)
    return database.list_samples(run_id)


@app.patch("/api/v1/samples/{sample_id:path}")
async def patch_sample(sample_id: str, payload: SamplePatch) -> dict[str, Any]:
    sample = database.update_sample(sample_id, payload.model_dump(exclude_none=True))
    if not sample:
        raise HTTPException(status_code=404, detail="样本不存在")
    return sample


def encode_export(samples: list[dict[str, Any]], output_format: str) -> list[str]:
    cleanlab_samples = bool(samples) and all(
        item.get("task_type") == "data_quality_audit" for item in samples
    )
    if cleanlab_samples and output_format in {"jsonl", "json"}:
        cleaned = []
        for item in samples:
            quality = item.get("quality", {})
            cleaned.append(
                {
                    "id": item["source"].get("row_id", item["id"]),
                    "text": item["question"],
                    "original_label": item["source"].get("original_label", ""),
                    "current_label": item["answer"],
                    "suggested_label": quality.get("suggested_label", ""),
                    "decision": quality.get("decision", "pending"),
                    "label_score": quality.get("label_score"),
                    "outlier_score": quality.get("outlier_score"),
                    "near_duplicate_score": quality.get("near_duplicate_score"),
                    "raw_record": item["source"].get("raw_record", {}),
                }
            )
        if output_format == "jsonl":
            return [json.dumps(item, ensure_ascii=False) for item in cleaned]
        return [json.dumps(cleaned, ensure_ascii=False, indent=2)]
    if output_format == "jsonl":
        return [json.dumps(item, ensure_ascii=False) for item in samples]
    if output_format == "json":
        return [json.dumps(samples, ensure_ascii=False, indent=2)]
    if output_format == "alpaca":
        return [
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
    if output_format in {"openai-ft", "ft", "chatml"}:
        return [
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": item["question"]},
                        {"role": "assistant", "content": item["answer"]},
                    ]
                },
                ensure_ascii=False,
            )
            for item in samples
        ]
    if output_format == "huggingface":
        return [
            json.dumps(
                {
                    "id": item["id"],
                    "instruction": item["question"],
                    "response": item["answer"],
                    "reasoning": item["reasoning"],
                    "metadata": {
                        "source": item["source"],
                        "generation": item["generation"],
                        "quality": item["quality"],
                    },
                },
                ensure_ascii=False,
            )
            for item in samples
        ]
    if output_format == "csv":
        stream = io.StringIO()
        writer = csv.writer(stream)
        if cleanlab_samples:
            writer.writerow(
                [
                    "id", "text", "original_label", "current_label", "suggested_label",
                    "decision", "label_score", "outlier_score", "near_duplicate_score",
                ]
            )
            for item in samples:
                quality = item.get("quality", {})
                writer.writerow(
                    [
                        item["source"].get("row_id", item["id"]),
                        item["question"],
                        item["source"].get("original_label", ""),
                        item["answer"],
                        quality.get("suggested_label", ""),
                        quality.get("decision", "pending"),
                        quality.get("label_score", ""),
                        quality.get("outlier_score", ""),
                        quality.get("near_duplicate_score", ""),
                    ]
                )
            return [stream.getvalue().rstrip("\n")]
        writer.writerow(["id", "task_type", "question", "answer", "reasoning", "human_status"])
        for item in samples:
            writer.writerow(
                [
                    item["id"],
                    item["task_type"],
                    item["question"],
                    item["answer"],
                    item["reasoning"],
                    item["quality"].get("human_status", "pending"),
                ]
            )
        return [stream.getvalue().rstrip("\n")]
    return [
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": item["question"]},
                    {"role": "assistant", "content": item["answer"]},
                ]
            },
            ensure_ascii=False,
        )
        for item in samples
    ]


@app.post("/api/v1/runs/{run_id}/export")
async def export_run(run_id: str, payload: ExportRequest) -> FileResponse:
    run = require_run(run_id)
    samples = database.list_samples(run_id)
    if not samples:
        raise HTTPException(status_code=409, detail="该运行没有可导出的样本")
    output_dir = settings.runtime_root / "exports" / run["project_id"] / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = (
        "csv"
        if payload.format == "csv"
        else "json"
        if payload.format == "json"
        else "jsonl"
    )
    filename = f"samples.{extension}" if payload.format in {"jsonl", "json", "csv"} else f"samples.{payload.format}.{extension}"
    output = ensure_within(output_dir / filename, settings.runtime_root)
    lines = encode_export(samples, payload.format)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Parse every line before returning the artifact.
    if payload.format == "json":
        json.loads(output.read_text(encoding="utf-8"))
    elif payload.format != "csv":
        for line in output.read_text(encoding="utf-8").splitlines():
            json.loads(line)
    return FileResponse(
        output,
        media_type=(
            "text/csv; charset=utf-8"
            if payload.format == "csv"
            else "application/json"
            if payload.format == "json"
            else "application/x-ndjson"
        ),
        filename=output.name,
        headers={"X-Sample-Count": str(len(samples)), "X-Parse-Validated": "true"},
    )


@app.post("/internal/llm/v1/chat/completions", include_in_schema=False)
async def llm_proxy(request: Request) -> StreamingResponse:
    if not settings.llm_ready:
        raise HTTPException(
            status_code=503,
            detail="LLM 密钥未完成轮换确认，拒绝外部模型调用",
        )
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="LLM 请求必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="LLM 请求体必须是对象")
    payload["model"] = settings.llm_model
    payload["thinking"] = {"type": "adaptive"}
    payload["reasoning_split"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=15))
    upstream = await client.send(
        client.build_request(
            "POST",
            f"{settings.llm_base_url}/chat/completions",
            content=body,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {settings.llm_api_key}",
            },
        ),
        stream=True,
    )
    if upstream.status_code >= 400:
        data = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=upstream.status_code,
            detail=data.decode("utf-8", errors="replace")[:1000],
        )

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


@app.post("/internal/vision/v1/chat/completions", include_in_schema=False)
async def vision_proxy(request: Request) -> StreamingResponse:
    if not settings.vision_ready:
        raise HTTPException(
            status_code=503,
            detail="视觉模型未配置，拒绝外部模型调用",
        )
    body = await request.body()
    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=15))
    upstream = await client.send(
        client.build_request(
            "POST",
            f"{settings.vision_base_url}/chat/completions",
            content=body,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {settings.vision_api_key}",
            },
        ),
        stream=True,
    )
    if upstream.status_code >= 400:
        data = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=upstream.status_code,
            detail=data.decode("utf-8", errors="replace")[:1000],
        )

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


# --------------------------- v2 page workflows ---------------------------


@app.get("/api/v2/catalog")
async def v2_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "synthetic-data-kit",
            "name": "Synthetic Data Kit",
            "summary": "从文档或既有对话生成 QA、CoT 和摘要，并完成质量筛选与格式转换。",
            "workflows": ["qa", "cot", "summary", "cot-enhance", "multimodal-qa"],
        },
        {
            "id": "easy-dataset",
            "name": "Easy Dataset",
            "summary": "通过本地 sidecar 完成文档问答、数据蒸馏、图片问答和评估数据生成。",
            "workflows": ["document-qa", "distillation", "image-qa", "evaluation"],
        },
        {
            "id": "synlogic",
            "name": "SynLogic",
            "summary": "生成 Arrow Maze 逻辑题，并使用上游规则验证器逐条校验。",
            "workflows": ["arrow-maze"],
        },
        {
            "id": "kaqg",
            "name": "KAQG",
            "summary": "从 PDF 构建 Neo4j 知识图谱，生成并评估难度可控的单选题。",
            "workflows": ["knowledge-graph-scq"],
        },
        {
            "id": "cleanlab",
            "name": "Cleanlab",
            "summary": "在本机检查文本分类数据中的错标签、异常和近重复样本。",
            "workflows": ["text-classification-audit"],
        },
    ]


@app.get("/api/v2/tools/{tool_id}/projects")
async def v2_list_projects(
    tool_id: str, include_archived: bool = Query(default=False)
) -> list[dict[str, Any]]:
    if tool_id not in TOOL_IDS:
        raise HTTPException(status_code=404, detail="工具不存在")
    return database.list_projects(tool_id, include_archived)


@app.post("/api/v2/tools/{tool_id}/projects", status_code=201)
async def v2_create_project(
    tool_id: str, payload: ToolProjectCreate
) -> dict[str, Any]:
    if tool_id not in TOOL_IDS or tool_id == "legacy":
        raise HTTPException(status_code=404, detail="工具不存在")
    try:
        return database.create_tool_project(tool_id, payload.name, payload.description)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该工具内已存在同名项目") from exc


@app.patch("/api/v2/projects/{project_id}")
async def v2_patch_project(
    project_id: str, payload: ToolProjectPatch
) -> dict[str, Any]:
    require_project(project_id)
    try:
        project = database.update_project(
            project_id,
            display_name=payload.name,
            description=payload.description,
            archived=payload.archived,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该工具内已存在同名项目") from exc
    return project or {}


@app.get("/api/v2/projects/{project_id}/assets")
async def v2_list_assets(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    return database.list_assets(project_id)


@app.post("/api/v2/projects/{project_id}/assets", status_code=201)
async def v2_upload_asset(
    project_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    project = require_project(project_id)
    if project.get("archived_at"):
        raise HTTPException(status_code=409, detail="归档项目不能上传文件")
    return await upload_asset(project_id, file)


@app.post("/api/v2/synthetic/projects/{project_id}/jobs", status_code=202)
async def v2_synthetic_job(
    project_id: str, payload: SyntheticJobCreate
) -> dict[str, Any]:
    require_tool_project(project_id, "synthetic-data-kit")
    validate_project_assets(project_id, payload.asset_ids)
    if payload.workflow_type == "cot-enhance":
        validate_cot_enhance_assets(project_id, payload.asset_ids)
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认本次生成参数")
    if payload.workflow_type == "multimodal-qa" and not settings.vision_ready:
        raise HTTPException(status_code=409, detail="视觉模型未配置，暂不能开始多模态生成")
    input_data = {
        "asset_ids": payload.asset_ids,
        "content_type": payload.workflow_type,
        "num_pairs": payload.num_pairs,
        "min_retained": payload.min_retained,
        "chunk_size": payload.chunk_size,
        "chunk_overlap": payload.chunk_overlap,
        "threshold": payload.threshold,
        "model": payload.model,
    }
    stages = stage_rows(
        ("check", "检查环境"),
        ("ingest", "解析输入"),
        ("generate", "生成数据"),
        ("curate", "质量筛选"),
        ("export", "整理结果"),
    )
    if payload.workflow_type == "summary":
        stages = [stage for stage in stages if stage["id"] != "curate"]
    run = database.create_run(
        project_id,
        "synthetic_generate_cot",
        input_data,
        workflow_type=payload.workflow_type,
        stages=stages,
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/easy-dataset/projects/{project_id}/jobs", status_code=202)
async def v2_easy_dataset_job(
    project_id: str, payload: EasyDatasetJobCreate
) -> dict[str, Any]:
    require_tool_project(project_id, "easy-dataset")
    validate_project_assets(project_id, payload.asset_ids)
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认本次生成参数")
    if payload.workflow_type == "image-qa" and not settings.vision_ready:
        raise HTTPException(status_code=409, detail="视觉模型未配置，暂不能开始图片问答")
    if payload.workflow_type in {"document-qa", "evaluation", "image-qa"} and not payload.asset_ids:
        raise HTTPException(status_code=422, detail="当前功能需要先上传输入文件")
    input_data = payload.model_dump(exclude={"confirmed"})
    if payload.workflow_type == "distillation":
        stages = stage_rows(
            ("check", "检查服务"),
            ("tags", "生成标签树"),
            ("generate", "生成问题与答案"),
            ("export", "整理结果"),
        )
    else:
        stages = stage_rows(
            ("check", "检查服务"),
            ("ingest", "导入并分块"),
            ("generate", "生成数据"),
            ("export", "整理结果"),
        )
    run = database.create_run(
        project_id,
        "easy_dataset_workflow",
        input_data,
        workflow_type=payload.workflow_type,
        stages=stages,
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/easy-dataset/projects/{project_id}/preview", status_code=202)
async def v2_easy_dataset_preview(
    project_id: str, payload: EasyDatasetPreviewRequest
) -> dict[str, Any]:
    require_tool_project(project_id, "easy-dataset")
    validate_project_assets(project_id, [payload.asset_id])
    run = database.create_run(
        project_id,
        "easy_dataset_ingest",
        {
            "asset_id": payload.asset_id,
            "project_name": f"workbench-{project_id[:8]}",
        },
        workflow_type="document-preview",
        stages=stage_rows(("ingest", "导入并分块")),
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/synlogic/projects/{project_id}/jobs", status_code=202)
async def v2_synlogic_job(
    project_id: str, payload: SynLogicJobCreate
) -> dict[str, Any]:
    require_tool_project(project_id, "synlogic")
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认本次生成参数")
    if payload.arrow_fill_rate_max < payload.arrow_fill_rate_min:
        raise HTTPException(status_code=422, detail="最大填充率不能小于最小填充率")
    input_data = payload.model_dump(exclude={"confirmed", "workflow_type"})
    run = database.create_run(
        project_id,
        "synlogic_generate_arrow_maze",
        input_data,
        workflow_type="arrow-maze",
        stages=stage_rows(
            ("generate", "生成迷宫"),
            ("verify", "逐条规则验证"),
            ("export", "整理结果"),
        ),
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/synlogic/projects/{project_id}/verify", status_code=202)
async def v2_synlogic_verify(
    project_id: str, payload: SynLogicVerifyRequest
) -> dict[str, Any]:
    require_tool_project(project_id, "synlogic")
    sample = database.get_sample(payload.sample_id)
    if not sample or sample["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="迷宫样本不存在")
    run = database.create_run(
        project_id,
        "synlogic_verify_arrow_maze",
        payload.model_dump(),
        workflow_type="arrow-maze-verify",
        stages=stage_rows(("verify", "规则验证")),
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/kaqg/projects/{project_id}/jobs", status_code=202)
async def v2_kaqg_job(project_id: str, payload: KaqgJobCreate) -> dict[str, Any]:
    require_tool_project(project_id, "kaqg")
    validate_project_assets(project_id, [payload.asset_id])
    asset = database.get_asset(payload.asset_id)
    if not asset or Path(asset["filename"]).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="KAQG 只接受有文本层的 PDF")
    total = sum(payload.difficulty_counts.model_dump().values())
    if not 1 <= total <= 30:
        raise HTTPException(status_code=422, detail="题目总数必须在 1 到 30 之间")
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认知识图谱和模型调用参数")
    run = database.create_run(
        project_id,
        "kaqg_generate_evaluate",
        payload.model_dump(exclude={"confirmed"}),
        workflow_type="knowledge-graph-scq",
        stages=stage_rows(
            ("check", "检查环境"),
            ("parse", "解析 PDF"),
            ("graph", "构建知识图谱"),
            ("generate", "生成试题"),
            ("evaluate", "评估试题"),
            ("export", "整理结果"),
        ),
    )
    manager.start(run["id"])
    return run


@app.post("/api/v2/cleanlab/projects/{project_id}/jobs", status_code=202)
async def v2_cleanlab_job(
    project_id: str, payload: CleanlabJobCreate
) -> dict[str, Any]:
    require_tool_project(project_id, "cleanlab")
    if payload.source_type == "asset":
        if not payload.asset_id:
            raise HTTPException(status_code=422, detail="请先选择 CSV 或 JSONL 数据文件")
        asset_ids = [payload.asset_id]
        if payload.pred_probs_asset_id:
            asset_ids.append(payload.pred_probs_asset_id)
        validate_project_assets(project_id, asset_ids)
        asset = database.get_asset(payload.asset_id)
        if not asset or Path(asset["filename"]).suffix.lower() not in {".csv", ".json", ".jsonl"}:
            raise HTTPException(status_code=422, detail="Cleanlab 只接受 CSV、JSON 或 JSONL")
    else:
        if not payload.source_job_id:
            raise HTTPException(status_code=422, detail="请选择一个已完成任务")
        source_run = database.get_run(payload.source_job_id)
        if not source_run or source_run["status"] != "succeeded":
            raise HTTPException(status_code=422, detail="只能导入已完成的平台任务")
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认本机数据质量检查参数")
    run = database.create_run(
        project_id,
        "cleanlab_audit_dataset",
        payload.model_dump(exclude={"confirmed"}),
        workflow_type="text-classification-audit",
        stages=stage_rows(
            ("parse", "读取并校验数据"),
            ("features", "构建文本特征"),
            ("audit", "检查数据质量"),
            ("export", "整理审核结果"),
        ),
    )
    manager.start(run["id"])
    return run


@app.patch("/api/v2/cleanlab/samples/{sample_id:path}/decision")
async def v2_cleanlab_decision(
    sample_id: str, payload: CleanlabDecisionRequest
) -> dict[str, Any]:
    sample = database.get_sample(sample_id)
    if not sample or sample["task_type"] != "data_quality_audit":
        raise HTTPException(status_code=404, detail="Cleanlab 审核样本不存在")
    original = str(sample["source"].get("original_label", ""))
    suggested = str(sample["quality"].get("suggested_label", ""))
    if payload.decision == "accept_suggestion":
        if not suggested:
            raise HTTPException(status_code=422, detail="该样本没有可接受的建议标签")
        current = suggested
    elif payload.decision == "keep_original":
        current = original
    else:
        if not payload.corrected_label or not payload.corrected_label.strip():
            raise HTTPException(status_code=422, detail="手动修改需要填写标签")
        current = payload.corrected_label.strip()
    updated = database.update_sample(
        sample_id,
        {
            "answer": current,
            "quality": {
                "decision": payload.decision,
                "human_status": "confirmed",
                "current_label": current,
            },
        },
    )
    return updated or {}


@app.get("/api/v2/jobs")
async def v2_list_jobs(
    project_id: str | None = None,
    tool_id: str | None = None,
    status: str | None = None,
    workflow_type: str | None = None,
    artifacts_only: bool = False,
    limit: int = Query(default=100, le=300),
) -> list[dict[str, Any]]:
    return database.list_runs(
        project_id,
        limit,
        tool_id=tool_id,
        status=status,
        workflow_type=workflow_type,
        artifacts_only=artifacts_only,
    )


@app.get("/api/v2/jobs/{job_id}")
async def v2_get_job(job_id: str) -> dict[str, Any]:
    run = require_run(job_id)
    project = require_project(run["project_id"])
    return {**run, "tool_id": project["tool_id"], "project_name": project["display_name"]}


@app.get("/api/v2/jobs/{job_id}/events")
async def v2_job_events(job_id: str, request: Request) -> StreamingResponse:
    return await run_events(job_id, request)


@app.get("/api/v2/jobs/{job_id}/event-log")
async def v2_job_event_log(job_id: str) -> list[dict[str, Any]]:
    require_run(job_id)
    return database.list_events(job_id)


@app.get("/api/v2/jobs/{job_id}/samples")
async def v2_job_samples(job_id: str) -> list[dict[str, Any]]:
    return await run_samples(job_id)


@app.post("/api/v2/jobs/{job_id}/export")
async def v2_export_job(job_id: str, payload: ExportRequest) -> FileResponse:
    return await export_run(job_id, payload)


@app.post("/api/v2/jobs/{job_id}/cancel")
async def v2_cancel_job(job_id: str) -> dict[str, Any]:
    return await cancel_run(job_id)


@app.get("/api/v2/jobs/{job_id}/artifacts/{artifact_name}")
async def v2_job_artifact(job_id: str, artifact_name: str) -> FileResponse:
    run = require_run(job_id)
    names = {
        "kaqg-graph": "kaqg-graph.json",
        "kaqg-questions": "kaqg-questions.jsonl",
        "kaqg-record": "kaqg-run-record.json",
        "cleanlab-audit": "cleanlab-audit.jsonl",
    }
    filename = names.get(artifact_name)
    if not filename:
        raise HTTPException(status_code=404, detail="产物不存在")
    path = ensure_within(
        settings.runtime_root / "runs" / run["project_id"] / job_id / filename,
        settings.runtime_root,
    )
    if not path.exists():
        raise HTTPException(status_code=404, detail="产物尚未生成")
    return FileResponse(path, filename=path.name)


async def integration_snapshot() -> dict[str, Any]:
    easy_reachable = False
    easy_error = ""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{settings.easy_dataset_base_url}/api/projects")
            easy_reachable = response.is_success
            if not response.is_success:
                easy_error = f"HTTP {response.status_code}"
    except Exception as exc:
        easy_error = type(exc).__name__
    kaqg_reachable = False
    kaqg_error = ""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{settings.kaqg_worker_base_url}/health")
            kaqg_reachable = response.is_success
            if not response.is_success:
                kaqg_error = f"HTTP {response.status_code}"
    except Exception as exc:
        kaqg_error = type(exc).__name__
    credential_state = (
        "verified"
        if PROBE_RESULTS.get("text", {}).get("ok")
        else "ready-to-probe"
        if settings.llm_ready
        else "rotation-unconfirmed"
        if settings.llm_key_present
        else "not-configured"
    )
    return {
        "text_model": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
            "judge_model": settings.llm_judge_model,
            "credential_state": credential_state,
            "provider_verified": credential_state == "verified",
            "message": (
                "文本模型最小请求已验证"
                if credential_state == "verified"
                else "新密钥已注入，等待最小请求验证"
                if credential_state == "ready-to-probe"
                else "已检测到密钥，但尚未确认轮换"
                if settings.llm_key_present
                else "未配置文本模型密钥"
            ),
        },
        "vision_model": {
            "base_url": settings.vision_base_url,
            "model": settings.vision_model,
            "credential_state": "configured" if settings.vision_ready else "not-configured",
            "message": "视觉模型已配置，尚未端到端验收" if settings.vision_ready else "视觉模型未配置",
        },
        "services": {
            "gateway": {
                "status": "reachable",
                "endpoint": settings.gateway_public_base_url,
                "version": app.version,
            },
            "easy_dataset": {
                "status": "reachable" if easy_reachable else "unreachable",
                "endpoint": settings.easy_dataset_base_url,
                "version": git_version(settings.easy_dataset_repo),
                "message": easy_error,
            },
            "synthetic_cli": {
                "status": "available" if settings.synthetic_cli.exists() else "missing",
                "path": str(settings.synthetic_cli),
                "version": git_version(settings.synthetic_repo),
            },
            "synlogic": {
                "status": "available" if settings.synlogic_repo.exists() else "missing",
                "path": str(settings.synlogic_repo),
                "version": git_version(settings.synlogic_repo),
            },
            "kaqg_worker": {
                "status": "reachable" if kaqg_reachable else "unreachable",
                "endpoint": settings.kaqg_worker_base_url,
                "version": git_version(settings.kaqg_repo),
                "message": kaqg_error,
            },
            "cleanlab": {
                "status": "available",
                "version": importlib.metadata.version("cleanlab"),
                "message": "本机运行，不使用外部模型",
            },
        },
    }


@app.get("/api/v2/integrations/status")
async def v2_integrations_status() -> dict[str, Any]:
    return await integration_snapshot()


def classify_probe_error(status_code: int | None, message: str) -> tuple[str, str]:
    lower = message.lower()
    if status_code == 401:
        return "invalid-credential", "401：密钥无效或已撤销"
    if status_code in {402, 403} or "balance" in lower or "insufficient" in lower:
        return "quota", "余额不足或账号无权访问"
    if status_code == 429:
        return "rate-limited", "请求被限流，请稍后重试"
    if status_code == 404 or "model" in lower and "not found" in lower:
        return "model-not-found", "模型名称不存在或当前账号不可用"
    return "endpoint-error", f"端点返回错误：{message[:240]}"


@app.post("/api/v2/integrations/{profile}/probe")
async def v2_probe(profile: str, payload: ProbeRequest) -> dict[str, Any]:
    if profile == "text":
        if not settings.llm_key_present:
            return {"ok": False, "code": "not-configured", "message": "未配置 MINIMAX_API_KEY"}
        if not settings.llm_credentials_rotated:
            return {
                "ok": False,
                "code": "rotation-unconfirmed",
                "message": "检测到密钥，但 LLM_CREDENTIAL_ROTATED 尚未设为 true；未发送外部请求",
            }
        # The gateway owns the cloud model selection; callers cannot override it.
        model = settings.llm_model
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
                response = await client.post(
                    f"{settings.llm_base_url}/chat/completions",
                    headers={"authorization": f"Bearer {settings.llm_api_key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [{"role": "user", "content": "只回复 OK"}],
                        "max_tokens": 8,
                        "temperature": 0,
                        "thinking": {"type": "adaptive"},
                        "reasoning_split": True,
                    },
                )
            if response.is_success:
                result = {
                    "ok": True,
                    "code": "verified",
                    "message": f"文本模型 {model} 最小请求成功",
                    "tested_at": now_iso(),
                }
                PROBE_RESULTS["text"] = result
                return result
            code, message = classify_probe_error(response.status_code, response.text)
            result = {
                "ok": False,
                "code": code,
                "message": message,
                "tested_at": now_iso(),
            }
            PROBE_RESULTS["text"] = result
            return result
        except httpx.RequestError as exc:
            return {
                "ok": False,
                "code": "network-unreachable",
                "message": f"网络不可达：{type(exc).__name__}",
            }
    if profile == "vision":
        if not settings.vision_ready:
            return {"ok": False, "code": "not-configured", "message": "视觉模型未配置；未发送请求"}
        return {
            "ok": False,
            "code": "end-to-end-not-verified",
            "message": "视觉凭据已配置，但当前版本不在设置页发送无图片探测请求",
        }
    if profile == "easy-dataset":
        snapshot = await integration_snapshot()
        item = snapshot["services"]["easy_dataset"]
        return {
            "ok": item["status"] == "reachable",
            "code": item["status"],
            "message": "Easy Dataset 服务可达" if item["status"] == "reachable" else "Easy Dataset 服务不可达",
        }
    if profile == "kaqg":
        snapshot = await integration_snapshot()
        item = snapshot["services"]["kaqg_worker"]
        return {
            "ok": item["status"] == "reachable",
            "code": item["status"],
            "message": "KAQG、Neo4j 与 MQTT 可达" if item["status"] == "reachable" else "KAQG worker 或依赖服务不可达",
        }
    if profile == "cleanlab":
        return {
            "ok": True,
            "code": "available",
            "message": f"Cleanlab {importlib.metadata.version('cleanlab')} 可用",
        }
    raise HTTPException(status_code=404, detail="未知的集成配置")


# ------------------------ v3 modules and data assets ---------------------


def require_pipeline(pipeline_id: str) -> dict[str, Any]:
    pipeline = database.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="流程不存在")
    return pipeline


def require_pipeline_run(pipeline_run_id: str) -> dict[str, Any]:
    pipeline_run = database.get_pipeline_run(pipeline_run_id)
    if not pipeline_run:
        raise HTTPException(status_code=404, detail="流程运行不存在")
    return pipeline_run


async def synthea_status() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{settings.synthea_worker_base_url}/health")
        if response.is_success:
            return {"status": "reachable", "message": "Synthea worker 可用", "detail": response.json()}
        return {"status": "unreachable", "message": f"HTTP {response.status_code}"}
    except httpx.RequestError as exc:
        return {"status": "unreachable", "message": type(exc).__name__}


@app.get("/api/v3/modules")
async def v3_modules() -> list[dict[str, Any]]:
    return [
        {"id": "toolbox", "name": "工具中心", "summary": "直接使用五个已集成工具。", "path": "/"},
        {"id": "domain", "name": "领域数据", "summary": "领域规则与专项验收；第一版提供医疗合成数据。", "path": "/medical"},
        {"id": "assets", "name": "数据资产", "summary": "查看不可覆盖的数据集版本、质量报告与血缘。", "path": "/datasets"},
    ]


@app.get("/api/v3/operators")
async def v3_operators() -> list[dict[str, Any]]:
    return OPERATORS


@app.get("/api/v3/pipelines/templates")
async def v3_pipeline_templates() -> list[dict[str, Any]]:
    return templates()


@app.get("/api/v3/pipelines")
async def v3_list_pipelines() -> list[dict[str, Any]]:
    return database.list_pipelines()


@app.post("/api/v3/pipelines", status_code=201)
async def v3_create_pipeline(payload: PipelineCreate) -> dict[str, Any]:
    value = payload.model_dump()
    if value["project_id"]:
        project = require_project(value["project_id"])
        if value["domain"] == "medical" and project["tool_id"] != "medical":
            raise HTTPException(status_code=409, detail="医疗流程只能关联医疗数据项目")
    verdict = validate_pipeline(value["nodes"], value["edges"])
    if not verdict["valid"]:
        raise HTTPException(status_code=422, detail=verdict)
    return database.create_pipeline(value)


@app.patch("/api/v3/pipelines/{pipeline_id}")
async def v3_update_pipeline(pipeline_id: str, payload: PipelinePatch) -> dict[str, Any]:
    pipeline = require_pipeline(pipeline_id)
    value = payload.model_dump(exclude_none=True)
    nodes = value.get("nodes", pipeline["nodes"])
    edges = value.get("edges", pipeline["edges"])
    verdict = validate_pipeline(nodes, edges)
    if not verdict["valid"]:
        raise HTTPException(status_code=422, detail=verdict)
    return database.update_pipeline(pipeline_id, value) or {}


@app.post("/api/v3/pipelines/{pipeline_id}/validate")
async def v3_validate_pipeline(pipeline_id: str) -> dict[str, Any]:
    pipeline = require_pipeline(pipeline_id)
    return validate_pipeline(pipeline["nodes"], pipeline["edges"])


@app.post("/api/v3/pipelines/{pipeline_id}/runs", status_code=202)
async def v3_start_pipeline(pipeline_id: str, payload: PipelineRunCreate) -> dict[str, Any]:
    pipeline = require_pipeline(pipeline_id)
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认本次流程参数")
    verdict = validate_pipeline(pipeline["nodes"], pipeline["edges"])
    if not verdict["valid"]:
        raise HTTPException(status_code=422, detail=verdict)
    if pipeline["domain"] != "medical":
        raise HTTPException(status_code=409, detail="当前通用模板用于保存和校验编排；请在工具中心执行实际生成任务后再登记到流程。医疗模板已支持本机端到端运行。")
    project_id = payload.project_id or pipeline.get("project_id")
    if not project_id:
        raise HTTPException(status_code=422, detail="医疗流程需要选择医疗项目")
    require_tool_project(project_id, "medical")
    return medical_manager.start(pipeline, project_id, payload.parameters)


@app.get("/api/v3/pipeline-runs/{pipeline_run_id}")
async def v3_get_pipeline_run(pipeline_run_id: str) -> dict[str, Any]:
    return require_pipeline_run(pipeline_run_id)


@app.get("/api/v3/pipeline-runs/{pipeline_run_id}/events")
async def v3_pipeline_events(pipeline_run_id: str, request: Request) -> StreamingResponse:
    require_pipeline_run(pipeline_run_id)

    async def stream():
        cursor = 0
        while True:
            if await request.is_disconnected():
                break
            events = database.list_pipeline_events(pipeline_run_id, cursor)
            for event in events:
                cursor = int(event["id"])
                yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            state = database.get_pipeline_run(pipeline_run_id)
            if state and state["status"] in {"succeeded", "needs_review", "failed", "cancelled"} and not events:
                yield f"event: end\ndata: {json.dumps({'status': state['status']})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v3/domain-packs")
async def v3_domain_packs() -> list[dict[str, Any]]:
    synthea = await synthea_status()
    return [
        {"id": "medical", "name": "医疗高保真数据", "status": synthea["status"], "generator": "Synthea", "formats": ["FHIR R4", "Bulk FHIR NDJSON", "CSV", "时间线 JSONL"], "message": synthea["message"]},
        {"id": "finance", "name": "金融数据", "status": "planned", "message": "后续评估 SDV 的许可证与种子数据边界。"},
        {"id": "embodied", "name": "具身智能数据", "status": "planned", "message": "后续优先评估 LeRobot 的导入、格式检查与质量分析。"},
    ]


@app.post("/api/v3/medical/projects/{project_id}/generate", status_code=202)
async def v3_generate_medical(project_id: str, payload: MedicalGenerateRequest) -> dict[str, Any]:
    require_tool_project(project_id, "medical")
    if payload.min_age > payload.max_age:
        raise HTTPException(status_code=422, detail="最小年龄不能大于最大年龄")
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认虚构患者生成参数")
    status = await synthea_status()
    if status["status"] != "reachable":
        raise HTTPException(status_code=503, detail=f"Synthea 服务未就绪：{status['message']}")
    medical_template = next(item for item in templates() if item["id"] == "medical-synthea")
    pipeline = database.create_pipeline({
        "name": f"医疗合成流程 {now_iso()[:10]}", "description": "由医疗领域页面创建的固定闭环。",
        "domain": "medical", "project_id": project_id, "nodes": medical_template["nodes"], "edges": medical_template["edges"],
    })
    try:
        resolve_output_policy(payload.output_policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return medical_manager.start(pipeline, project_id, payload.model_dump(exclude={"confirmed"}))


@app.get("/api/v3/medical/output-catalog")
async def v3_medical_output_catalog() -> list[dict[str, Any]]:
    return medical_output_catalog()


@app.post("/api/v3/medical/generations", status_code=202)
async def v3_create_medical_generation(payload: MedicalGenerationCreate) -> dict[str, Any]:
    if payload.min_age > payload.max_age:
        raise HTTPException(status_code=422, detail="最小年龄不能大于最大年龄")
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="请先确认虚构患者生成参数")
    try:
        resolved_policy, reasons = resolve_output_policy(payload.output_policy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    status = await synthea_status()
    if status["status"] != "reachable":
        raise HTTPException(status_code=503, detail=f"Synthea 服务未就绪：{status['message']}")
    try:
        project = database.create_tool_project("medical", payload.name.strip(), "每次生成自动创建的医疗合成数据项目。")
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="项目名称已存在，请换一个名称") from exc
    medical_template = next(item for item in templates() if item["id"] == "medical-synthea")
    pipeline = database.create_pipeline({
        "name": f"医疗合成流程 {now_iso()[:10]}", "description": "医疗页面固定闭环流程。",
        "domain": "medical", "project_id": project["id"], "nodes": medical_template["nodes"], "edges": medical_template["edges"],
    })
    parameters = payload.model_dump(exclude={"name", "confirmed"})
    parameters["output_policy"] = payload.output_policy
    parameters["resolved_output_policy"] = resolved_policy
    parameters["dependency_reasons"] = reasons
    run = medical_manager.start(pipeline, project["id"], parameters)
    return {**run, "project": project, "resolved_output_policy": resolved_policy, "dependency_reasons": reasons}


@app.get("/api/v3/medical/projects")
async def v3_medical_projects() -> list[dict[str, Any]]:
    projects = database.list_projects("medical", include_archived=False)
    datasets = database.list_datasets()
    result: list[dict[str, Any]] = []
    for project in projects:
        project_datasets = [item for item in datasets if item.get("project_id") == project["id"]]
        latest = project_datasets[0] if project_datasets else None
        latest_run = database.get_latest_pipeline_run(project["id"])
        quality = (latest or {}).get("quality") or {}
        rules = quality.get("rules") or {}
        latest_summary = None
        if latest:
            latest_summary = {key: latest.get(key) for key in ("id", "name", "project_id", "created_at", "version_id", "version", "status")}
        result.append({
            **project,
            "dataset_count": len(project_datasets),
            "latest_pipeline_run_id": latest_run["id"] if latest_run else None,
            "latest_pipeline_run_status": latest_run["status"] if latest_run else None,
            # 项目列表只返回摘要，避免把完整 Manifest/质量报告重复带给前端。
            "latest_dataset": latest_summary,
            "quality_passed": quality.get("passed"),
            "resource_count": rules.get("resource_count", 0),
            "timeline_count": rules.get("timeline_count", 0),
            "task_count": rules.get("task_count", 0),
        })
    return result


def require_medical_artifact(version_id: str, artifact_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    version = database.get_dataset_version(version_id)
    if not version or version.get("domain") != "medical":
        raise HTTPException(status_code=404, detail="医疗数据集版本不存在")
    artifact = next((item for item in version.get("manifest", {}).get("artifacts", []) if item.get("id") == artifact_id), None)
    if not artifact or artifact.get("mode") != "publish":
        raise HTTPException(status_code=404, detail="产物不存在或尚未发布")
    root = Path(version.get("artifact_path") or "")
    path = ensure_within(root / str(artifact["relative_path"]), settings.runtime_root)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="产物文件不存在")
    return version, artifact, path


@app.get("/api/v3/medical/projects/{project_id}/assets")
async def v3_medical_project_assets(project_id: str) -> dict[str, Any]:
    project = require_tool_project(project_id, "medical")
    datasets = [item for item in database.list_datasets() if item.get("project_id") == project_id]
    versions = [version for dataset in datasets for version in database.list_dataset_versions(dataset["id"])]
    visible: list[dict[str, Any]] = []
    for version in versions:
        manifest = version.get("manifest") or {}
        visible.append({**version, "artifacts": [item for item in manifest.get("artifacts", []) if item.get("mode") == "publish"]})
    return {"project": project, "versions": visible}


@app.get("/api/v3/dataset-versions/{version_id}/artifacts/{artifact_id}/preview")
async def v3_preview_medical_artifact(version_id: str, artifact_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    _, artifact, path = require_medical_artifact(version_id, artifact_id)
    rows: list[Any] = []
    total = int(artifact.get("row_count") or 0)
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader):
                if index < offset:
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    break
        return {"artifact": artifact, "offset": offset, "limit": limit, "total": total, "columns": artifact.get("columns", []), "rows": rows}
    if path.suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index < offset or not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"raw": line.rstrip()})
                if len(rows) >= limit:
                    break
        return {"artifact": artifact, "offset": offset, "limit": limit, "total": total, "rows": rows}
    content = path.read_text(encoding="utf-8")[:50000]
    return {"artifact": artifact, "offset": 0, "limit": 1, "total": 1, "content": content}


@app.get("/api/v3/dataset-versions/{version_id}/artifacts/{artifact_id}/download")
async def v3_download_medical_artifact(version_id: str, artifact_id: str) -> FileResponse:
    _, artifact, path = require_medical_artifact(version_id, artifact_id)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/v3/datasets")
async def v3_list_datasets() -> list[dict[str, Any]]:
    return database.list_datasets()


@app.get("/api/v3/datasets/{dataset_id}/versions")
async def v3_dataset_versions(dataset_id: str) -> list[dict[str, Any]]:
    return database.list_dataset_versions(dataset_id)


@app.post("/api/v3/datasets/{dataset_id}/versions/{version_id}/publish")
async def v3_publish_dataset(dataset_id: str, version_id: str) -> dict[str, Any]:
    version = database.get_dataset_version(version_id)
    if not version or version["dataset_id"] != dataset_id:
        raise HTTPException(status_code=404, detail="数据集版本不存在")
    try:
        return database.publish_dataset_version(version_id) or {}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v3/datasets/{dataset_id}/lineage")
async def v3_dataset_lineage(dataset_id: str) -> list[dict[str, Any]]:
    versions = database.list_dataset_versions(dataset_id)
    if not versions:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return [event for version in versions for event in database.list_lineage_events(version["id"])]


@app.get("/api/v3/integrations/status")
async def v3_integrations_status() -> dict[str, Any]:
    snapshot = await integration_snapshot()
    snapshot["services"]["synthea_worker"] = {**await synthea_status(), "endpoint": settings.synthea_worker_base_url, "version": settings.synthea_commit}
    return snapshot
