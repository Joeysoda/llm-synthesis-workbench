from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from .contracts import (
    RISK_ORDER,
    SCENARIO_ALL,
    ExecutionContext,
    SafetyPolicy,
    ScenarioMode,
    ToolDefinition,
    ToolTier,
)

MCPProvider = Callable[[], list[ToolDefinition] | Awaitable[list[ToolDefinition]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._mcp_providers: list[MCPProvider] = []

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def register_many(self, tools: list[ToolDefinition]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> ToolDefinition | None:
        if name in self._tools:
            return self._tools[name]
        return next(
            (tool for tool in self._tools.values() if name in tool.aliases),
            None,
        )

    def list_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def connect_mcp(self, provider: MCPProvider) -> None:
        self._mcp_providers.append(provider)

    def disconnect_mcp(self, provider: MCPProvider) -> None:
        if provider in self._mcp_providers:
            self._mcp_providers.remove(provider)

    async def assemble_tool_pool(
        self,
        mode: ScenarioMode = SCENARIO_ALL,
        safety: SafetyPolicy | None = None,
        context: ExecutionContext | None = None,
    ) -> list[ToolDefinition]:
        del context
        policy = safety or SafetyPolicy()

        # 1. Enumerate
        candidates = list(self._tools.values())

        # 2. Mode Filter
        if mode.name != SCENARIO_ALL.name and mode.required_tags:
            candidates = [
                tool for tool in candidates if set(tool.tags) & mode.required_tags
            ]

        # 3. Safety Gate
        def allowed(tool: ToolDefinition) -> bool:
            return (
                tool.name not in policy.denied_tools
                and tool.tier in policy.allowed_tiers
                and RISK_ORDER[tool.risk_level]
                <= RISK_ORDER[policy.max_allowed_risk]
                and (policy.allow_open_world or not tool.hints.open_world)
            )

        candidates = [tool for tool in candidates if allowed(tool)]

        # 4. MCP Merge
        for provider in self._mcp_providers:
            value = provider()
            mcp_tools = await value if inspect.isawaitable(value) else value
            candidates.extend(
                tool
                for tool in mcp_tools
                if allowed(tool)
                and (
                    mode.name == SCENARIO_ALL.name
                    or not mode.required_tags
                    or set(tool.tags) & mode.required_tags
                )
            )

        # 5. Dedup: builtin > domain > external
        priority = {ToolTier.BUILTIN: 3, ToolTier.DOMAIN: 2, ToolTier.EXTERNAL: 1}
        deduped: dict[str, ToolDefinition] = {}
        for tool in candidates:
            existing = deduped.get(tool.name)
            if not existing or priority[tool.tier] > priority[existing.tier]:
                deduped[tool.name] = tool

        available: list[ToolDefinition] = []
        for tool in deduped.values():
            if await tool.is_available():
                available.append(tool)
        return sorted(available, key=lambda item: item.name)

    def generate_tool_descriptions(
        self, pool: list[ToolDefinition], deferred: bool = False
    ) -> list[dict]:
        return [tool.to_public_dict(deferred=deferred) for tool in pool]
