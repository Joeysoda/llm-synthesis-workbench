from __future__ import annotations

import inspect
import time
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class ToolTier(str, Enum):
    BUILTIN = "builtin"
    DOMAIN = "domain"
    EXTERNAL = "external"


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class BehaviorHints(BaseModel):
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


class ToolError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    success: bool
    data: Any = None
    summary: str
    error: ToolError | None = None
    persisted_path: str | None = None
    duration_ms: float = 0


class ExecutionContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    task_node_id: str
    iteration_count: int = 0
    token_budget_used: int = 0
    remaining_context_chars: int = 0
    user_trust_level: str = "user"
    working_directory: str
    project_id: str
    run_id: str
    emit_fn: Any = Field(default=None, exclude=True)
    cancel_event: Any = Field(default=None, exclude=True)

    @property
    def run_dir(self) -> Path:
        return Path(self.working_directory)

    async def emit(
        self, message: str, event_type: str = "log", data: dict[str, Any] | None = None
    ) -> None:
        if self.emit_fn:
            result = self.emit_fn(message, event_type, data or {})
            if inspect.isawaitable(result):
                await result

    def cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())


class ScenarioMode(BaseModel):
    name: str
    required_tags: set[str] = Field(default_factory=set)


SCENARIO_ALL = ScenarioMode(name="all")
SCENARIO_DOCUMENT = ScenarioMode(
    name="document_synthesis",
    required_tags={"document-synthesis", "data-processing", "dataset-construction"},
)
SCENARIO_COT = ScenarioMode(
    name="cot_generation",
    required_tags={"cot-generation", "reasoning", "dataset-construction"},
)
SCENARIO_LOGIC = ScenarioMode(
    name="logic_generation",
    required_tags={"logic-generation", "verification", "dataset-construction"},
)


class SafetyPolicy(BaseModel):
    denied_tools: set[str] = Field(default_factory=set)
    max_allowed_risk: RiskLevel = RiskLevel.MEDIUM
    allowed_tiers: set[ToolTier] = Field(
        default_factory=lambda: {ToolTier.BUILTIN, ToolTier.DOMAIN, ToolTier.EXTERNAL}
    )
    allow_open_world: bool = True


ExecuteFn = Callable[[BaseModel, ExecutionContext], Awaitable[ToolResult] | ToolResult]
PermissionFn = Callable[[BaseModel, ExecutionContext], Awaitable[bool] | bool]
AvailabilityFn = Callable[[], Awaitable[bool] | bool]


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    short_description: str
    input_schema: type[BaseModel] = Field(exclude=True)
    output_schema: type[BaseModel] | None = Field(default=None, exclude=True)
    execute_fn: ExecuteFn = Field(exclude=True)
    check_permission_fn: PermissionFn | None = Field(default=None, exclude=True)
    is_available_fn: AvailabilityFn | None = Field(default=None, exclude=True)
    aliases: list[str] = Field(default_factory=list)
    tier: ToolTier = ToolTier.DOMAIN
    version: str = "0.1.0"
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    hints: BehaviorHints = Field(default_factory=BehaviorHints)
    timeout_ms: int = 30_000
    supports_streaming: bool = False
    risk_level: RiskLevel = RiskLevel.MEDIUM
    required_trust_level: str = "user"
    max_result_size_chars: int = 50_000
    mcp_metadata: dict[str, Any] | None = None

    async def is_available(self) -> bool:
        if not self.is_available_fn:
            return True
        value = self.is_available_fn()
        return bool(await value) if inspect.isawaitable(value) else bool(value)

    async def check_permission(self, params: BaseModel, ctx: ExecutionContext) -> bool:
        if not self.check_permission_fn:
            return True
        value = self.check_permission_fn(params, ctx)
        return bool(await value) if inspect.isawaitable(value) else bool(value)

    async def execute(self, raw_params: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        started = time.perf_counter()
        params = self.input_schema.model_validate(raw_params)
        if not await self.check_permission(params, ctx):
            return ToolResult(
                success=False,
                summary="工具权限检查未通过",
                error=ToolError(code="permission_denied", message="Tool permission denied"),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        value = self.execute_fn(params, ctx)
        result = await value if inspect.isawaitable(value) else value
        if result.duration_ms == 0:
            result.duration_ms = (time.perf_counter() - started) * 1000
        return result

    def to_public_dict(self, deferred: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "short_description": self.short_description,
            "tier": self.tier.value,
            "version": self.version,
            "tags": self.tags,
            "risk_level": self.risk_level.value,
            "hints": self.hints.model_dump(),
        }
        if not deferred:
            payload.update(
                {
                    "description": self.description,
                    "input_schema": self.input_schema.model_json_schema(),
                    "output_schema": (
                        self.output_schema.model_json_schema() if self.output_schema else None
                    ),
                    "depends_on": self.depends_on,
                    "timeout_ms": self.timeout_ms,
                }
            )
        return payload


def build_tool(
    *,
    name: str,
    description: str,
    input_schema: type[BaseModel],
    execute: ExecuteFn,
    short_description: str | None = None,
    output_schema: type[BaseModel] | None = None,
    aliases: list[str] | None = None,
    tier: ToolTier = ToolTier.DOMAIN,
    version: str = "0.1.0",
    tags: list[str] | None = None,
    depends_on: list[str] | None = None,
    hints: BehaviorHints | None = None,
    timeout_ms: int = 30_000,
    supports_streaming: bool = False,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    check_permission: PermissionFn | None = None,
    is_available: AvailabilityFn | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        short_description=short_description or description,
        input_schema=input_schema,
        output_schema=output_schema,
        execute_fn=execute,
        check_permission_fn=check_permission,
        is_available_fn=is_available,
        aliases=aliases or [],
        tier=tier,
        version=version,
        tags=tags or [],
        depends_on=depends_on or [],
        hints=hints or BehaviorHints(),
        timeout_ms=timeout_ms,
        supports_streaming=supports_streaming,
        risk_level=risk_level,
    )
