from __future__ import annotations

import json
import asyncio
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, model_validator

from ..config import Settings
from ..contracts import BehaviorHints, ExecutionContext, RiskLevel, ToolError, ToolResult, build_tool
from ..database import Database
from ..security import ensure_within


class DifficultyCounts(BaseModel):
    easy: int = Field(default=3, ge=0, le=10)
    medium: int = Field(default=3, ge=0, le=10)
    hard: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def validate_total(self):
        total = self.easy + self.medium + self.hard
        if not 1 <= total <= 30:
            raise ValueError("题目总数必须在 1 到 30 之间")
        return self


class KaqgInput(BaseModel):
    asset_id: str
    subject_name: str = Field(min_length=1, max_length=120)
    difficulty_counts: DifficultyCounts = Field(default_factory=DifficultyCounts)


def git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def build_kaqg_tools(database: Database, settings: Settings) -> list:
    commit = git_commit(settings.kaqg_repo)
    if commit == "unknown":
        commit = settings.kaqg_commit

    async def call_worker(
        params: KaqgInput, ctx: ExecutionContext, *, graph_only: bool
    ) -> ToolResult:
        asset = database.get_asset(params.asset_id)
        if not asset or asset["project_id"] != ctx.project_id:
            return ToolResult(
                success=False,
                summary="KAQG 输入文件不存在",
                error=ToolError(code="kaqg_asset_not_found", message="PDF 不属于当前项目"),
            )
        source = ensure_within(Path(asset["path"]), settings.runtime_root)
        if source.suffix.lower() != ".pdf":
            return ToolResult(
                success=False,
                summary="KAQG 只接受 PDF",
                error=ToolError(code="kaqg_pdf_required", message="请选择有文本层的 PDF 文件"),
            )
        if not settings.llm_ready:
            return ToolResult(
                success=False,
                summary="KAQG 文本模型尚未就绪",
                error=ToolError(
                    code="llm_not_ready",
                    message="请先配置并确认轮换后的 DeepSeek 文本模型密钥",
                ),
            )
        await ctx.emit("检查 KAQG worker、Neo4j 与 Mosquitto", "stage", {"stage_id": "check"})
        payload = {
            "pdf_path": str(source),
            "run_dir": str(ctx.run_dir),
            "project_id": ctx.project_id,
            "run_id": ctx.run_id,
            "subject_name": params.subject_name,
            "difficulty_counts": params.difficulty_counts.model_dump(),
            "model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            "llm_api_key": settings.deepseek_api_key,
            "kaqg_commit": commit,
            "graph_only": graph_only,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10)) as client:
                request_task = asyncio.create_task(
                    client.post(f"{settings.kaqg_worker_base_url}/run", json=payload)
                )
                reported_stage = "check"
                while not request_task.done():
                    await asyncio.sleep(1)
                    try:
                        status_response = await client.get(
                            f"{settings.kaqg_worker_base_url}/runs/{ctx.run_id}/status"
                        )
                        if not status_response.is_success:
                            continue
                        status = status_response.json()
                        stage_id = str(status.get("stage", ""))
                        if stage_id and stage_id != reported_stage:
                            reported_stage = stage_id
                            await ctx.emit(
                                str(status.get("message", "KAQG 正在执行")),
                                "stage",
                                {
                                    "stage_id": stage_id,
                                    "progress": status.get("progress", {}),
                                },
                            )
                        elif status.get("progress"):
                            await ctx.emit(
                                str(status.get("message", "KAQG 正在执行")),
                                "metric",
                                {"progress": status["progress"]},
                            )
                    except httpx.RequestError:
                        continue
                response = await request_task
            if not response.is_success:
                message = response.text[:1000]
                try:
                    message = response.json().get("detail", message)
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    summary="KAQG worker 执行失败",
                    error=ToolError(code="kaqg_worker_failed", message=str(message)),
                )
            result = response.json()
        except httpx.RequestError as exc:
            return ToolResult(
                success=False,
                summary="KAQG worker 不可达",
                error=ToolError(
                    code="kaqg_worker_unreachable",
                    message=f"无法连接 KAQG worker：{type(exc).__name__}",
                ),
            )
        for stage_id, label in (
            ("parse", "PDF 文本解析完成"),
            ("graph", "知识图谱构建完成"),
            ("generate", "难度可控试题生成完成"),
            ("evaluate", "试题评估完成"),
            ("export", "图谱与试题产物已保存"),
        ):
            if graph_only and stage_id in {"generate", "evaluate"}:
                continue
            if stage_id != reported_stage:
                await ctx.emit(label, "stage", {"stage_id": stage_id})
        samples = result.pop("samples", [])
        for index, sample in enumerate(samples):
            sample["id"] = f"{ctx.run_id}/{index + 1}"
            sample.setdefault("generation", {})["commit"] = commit
        result["samples"] = samples
        return ToolResult(
            success=True,
            data=result,
            summary=(
                f"KAQG 已构建 {result.get('node_count', 0)} 个节点、"
                f"{result.get('relationship_count', 0)} 条关系并生成 {len(samples)} 道题"
            ),
            persisted_path=result.get("artifact_path"),
        )

    async def build_graph(params: KaqgInput, ctx: ExecutionContext) -> ToolResult:
        return await call_worker(params, ctx, graph_only=True)

    async def generate_evaluate(params: KaqgInput, ctx: ExecutionContext) -> ToolResult:
        return await call_worker(params, ctx, graph_only=False)

    common = {
        "input_schema": KaqgInput,
        "tags": ["knowledge-graph", "question-generation", "dataset-construction"],
        "hints": BehaviorHints(read_only=False, destructive=False, idempotent=False, open_world=True),
        "risk_level": RiskLevel.MEDIUM,
        "timeout_ms": 1_800_000,
        "is_available": lambda: settings.kaqg_repo.exists(),
    }
    return [
        build_tool(
            name="kaqg_build_graph",
            description="使用 KAQG PDF 抽取和固定 Neo4j sidecar 构建项目知识图谱。",
            short_description="从 PDF 构建知识图谱",
            execute=build_graph,
            **common,
        ),
        build_tool(
            name="kaqg_generate_evaluate",
            description="执行 KAQG PDF、知识图谱、难度可控单选题和评估闭环。",
            short_description="生成并评估知识图谱增强试题",
            execute=generate_evaluate,
            **common,
        ),
    ]
