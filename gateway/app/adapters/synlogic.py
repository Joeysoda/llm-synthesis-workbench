from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

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


class ArrowMazeGenerateInput(BaseModel):
    num_of_data: int = Field(default=20, ge=1, le=1000)
    width: int = Field(default=5, ge=4, le=12)
    height: int = Field(default=5, ge=4, le=12)
    arrow_fill_rate_min: float = Field(default=0.3, ge=0, le=1)
    arrow_fill_rate_max: float = Field(default=0.9, ge=0, le=1)
    max_attempts: int = Field(default=10000, ge=100, le=100000)

    @field_validator("arrow_fill_rate_max")
    @classmethod
    def validate_range(cls, value: float, info) -> float:
        minimum = info.data.get("arrow_fill_rate_min", 0)
        if value < minimum:
            raise ValueError("最大填充率不能小于最小填充率")
        return value


class ArrowMazeVerifyInput(BaseModel):
    sample_id: str
    answer: list[list[str]]


def build_synlogic_tools(database: Database, settings: Settings) -> list:
    worker = settings.system_root / "gateway" / "workers" / "synlogic_worker.py"
    commit = git_commit(settings.synlogic_repo)

    async def generate(
        params: ArrowMazeGenerateInput, ctx: ExecutionContext
    ) -> ToolResult:
        await ctx.emit(
            "调用 Arrow Maze 原始生成器",
            "stage",
            {"stage_id": "generate"},
        )
        payload_path = ctx.run_dir / "payload.json"
        result_path = ctx.run_dir / "result.json"
        payload_path.write_text(
            json.dumps({"action": "generate", **params.model_dump()}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = await run_command(
            [
                sys.executable,
                worker,
                "--repo",
                settings.synlogic_repo,
                "--payload",
                payload_path,
                "--output",
                result_path,
            ],
            cwd=settings.system_root,
            ctx=ctx,
            timeout_seconds=180,
        )
        if command_failed(result) or not result_path.exists():
            return ToolResult(
                success=False,
                summary="SynLogic 迷宫生成失败",
                error=ToolError(
                    code="synlogic_generation_failed",
                    message="原始生成器未产生有效结果",
                ),
            )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        await ctx.emit(
            "逐条读取原始验证器结果",
            "stage",
            {"stage_id": "verify"},
        )
        samples: list[dict[str, Any]] = []
        for index, row in enumerate(data.pop("rows")):
            metadata = row.get("metadata") or {}
            answer = row.get("answer", "")
            if isinstance(answer, str):
                try:
                    answer = json.dumps(
                        json.loads(answer), ensure_ascii=False, separators=(",", ":")
                    )
                except json.JSONDecodeError:
                    pass
            samples.append(
                {
                    "id": f"{ctx.run_id}/{index + 1}",
                    "task_type": "arrow_maze",
                    "question": row.get("question", ""),
                    "answer": answer,
                    "reasoning": "",
                    "source": {
                        "file": "SynLogic Arrow Maze",
                        "chunk_id": metadata.get("trace_id", ""),
                        "evidence": "确定性迷宫规则与完整解答网格",
                        "maze": metadata.get("maze"),
                        "solution": metadata.get("solution"),
                    },
                    "generation": {
                        "tool": "synlogic",
                        "commit": commit,
                        "model": "deterministic-generator",
                        "config": params.model_dump(),
                    },
                    "quality": {
                        "rule_passed": bool(row.get("verified")),
                        "model_score": 0,
                        "human_status": "pending",
                    },
                }
            )
        data["samples"] = samples
        await ctx.emit(
            f"生成 {data['generated']} 条，规则验证通过 {data['verified']} 条",
            "metric",
            {
                "generated": data["generated"],
                "verified": data["verified"],
                "negative_rejected": data["negative_rejected"],
            },
        )
        await ctx.emit(
            "整理原始与统一格式产物",
            "stage",
            {"stage_id": "export"},
        )
        return ToolResult(
            success=(
                data["generated"] == params.num_of_data
                and data["verified"] == data["generated"]
                and data["negative_rejected"]
            ),
            data=data,
            summary=f"SynLogic 已生成并验证 {data['verified']}/{data['generated']} 条迷宫",
            persisted_path=data["raw_path"],
        )

    async def verify(
        params: ArrowMazeVerifyInput, ctx: ExecutionContext
    ) -> ToolResult:
        sample = database.get_sample(params.sample_id)
        if not sample or sample["task_type"] != "arrow_maze":
            return ToolResult(
                success=False,
                summary="找不到迷宫样本",
                error=ToolError(code="sample_not_found", message="Maze sample not found"),
            )
        row = {
            "question": sample["question"],
            "answer": sample["answer"],
            "difficulty": 1,
            "metadata": {
                "maze": sample["source"].get("maze"),
                "solution": sample["source"].get("solution"),
            },
        }
        payload_path = ctx.run_dir / "verify-payload.json"
        result_path = ctx.run_dir / "verify-result.json"
        payload_path.write_text(
            json.dumps(
                {"action": "verify", "row": row, "answer": params.answer},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = await run_command(
            [
                sys.executable,
                worker,
                "--repo",
                settings.synlogic_repo,
                "--payload",
                payload_path,
                "--output",
                result_path,
            ],
            cwd=settings.system_root,
            ctx=ctx,
            timeout_seconds=30,
        )
        if command_failed(result) or not result_path.exists():
            return ToolResult(
                success=False,
                summary="迷宫验证器执行失败",
                error=ToolError(code="synlogic_verifier_failed", message="Verifier failed"),
            )
        passed = bool(json.loads(result_path.read_text(encoding="utf-8"))["passed"])
        return ToolResult(
            success=True,
            data={"passed": passed, "sample_id": params.sample_id},
            summary="答案验证通过" if passed else "答案未通过规则验证",
            persisted_path=str(result_path),
        )

    return [
        build_tool(
            name="synlogic_generate_arrow_maze",
            description="调用 SynLogic 原始 Arrow Maze 生成器创建迷宫并全量运行确定性验证器。",
            short_description="生成并验证 Arrow Maze",
            input_schema=ArrowMazeGenerateInput,
            execute=generate,
            tier=ToolTier.DOMAIN,
            version=commit[:7],
            tags=["logic-generation", "verification", "dataset-construction"],
            hints=BehaviorHints(idempotent=False, open_world=False),
            timeout_ms=180_000,
            risk_level=RiskLevel.MEDIUM,
            is_available=lambda: settings.synlogic_repo.exists(),
        ),
        build_tool(
            name="synlogic_verify_arrow_maze",
            description="使用 SynLogic 原始 ArrowMazeVerifier 校验指定迷宫样本的二维答案网格。",
            short_description="验证 Arrow Maze 答案",
            input_schema=ArrowMazeVerifyInput,
            execute=verify,
            tier=ToolTier.DOMAIN,
            version=commit[:7],
            tags=["logic-generation", "verification"],
            hints=BehaviorHints(read_only=True, idempotent=True, open_world=False),
            timeout_ms=30_000,
            risk_level=RiskLevel.LOW,
            is_available=lambda: settings.synlogic_repo.exists(),
        ),
    ]
