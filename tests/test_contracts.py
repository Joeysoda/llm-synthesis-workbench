from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.contracts import (
    BehaviorHints,
    ExecutionContext,
    RiskLevel,
    SafetyPolicy,
    ToolResult,
    ToolTier,
    build_tool,
)
from app.registry import ToolRegistry
from app.security import ensure_within, redact_secrets, sanitize_filename


class EmptyInput(BaseModel):
    pass


async def noop(params, context):
    del params, context
    return ToolResult(success=True, summary="ok")


@pytest.mark.asyncio
async def test_five_step_pool_filters_risk_and_deduplicates(tmp_path):
    registry = ToolRegistry()
    registry.register(
        build_tool(
            name="same",
            description="external",
            input_schema=EmptyInput,
            execute=noop,
            tier=ToolTier.EXTERNAL,
            risk_level=RiskLevel.LOW,
            tags=["verification"],
        )
    )

    async def mcp_provider():
        return [
            build_tool(
                name="same",
                description="builtin",
                input_schema=EmptyInput,
                execute=noop,
                tier=ToolTier.BUILTIN,
                risk_level=RiskLevel.LOW,
                tags=["verification"],
            ),
            build_tool(
                name="blocked",
                description="high",
                input_schema=EmptyInput,
                execute=noop,
                tier=ToolTier.BUILTIN,
                risk_level=RiskLevel.HIGH,
                tags=["verification"],
            ),
        ]

    registry.connect_mcp(mcp_provider)
    pool = await registry.assemble_tool_pool(
        safety=SafetyPolicy(max_allowed_risk=RiskLevel.MEDIUM),
        context=ExecutionContext(
            session_id="s",
            task_node_id="t",
            working_directory=str(tmp_path),
            project_id="p",
            run_id="r",
        ),
    )
    assert [item.name for item in pool] == ["same"]
    assert pool[0].tier == ToolTier.BUILTIN


def test_security_boundaries_and_redaction(tmp_path):
    assert sanitize_filename("../中文 文档.docx") == "中文_文档.docx"
    with pytest.raises(ValueError):
        sanitize_filename("payload.py")
    with pytest.raises(ValueError):
        ensure_within(tmp_path.parent / "escape", tmp_path)
    redacted = redact_secrets("Authorization: Bearer abc123 api_key=sk-secret123456")
    assert "abc123" not in redacted
    assert "sk-secret" not in redacted


def test_legacy_runtime_path_can_be_remapped_inside_current_root(tmp_path):
    current_root = tmp_path / "container" / "runtime"
    current_file = current_root / "uploads" / "project" / "asset" / "input.md"
    current_file.parent.mkdir(parents=True)
    current_file.write_text("data", encoding="utf-8")

    legacy_path = tmp_path / "mac" / "runtime" / "uploads" / "project" / "asset" / "input.md"
    assert ensure_within(legacy_path, current_root) == current_file.resolve()
