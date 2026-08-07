from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any

from .config import Settings
from .contracts import ExecutionContext, RiskLevel, ToolError, ToolResult
from .database import Database, now_iso
from .registry import ToolRegistry
from .security import redact_secrets


class RunManager:
    def __init__(
        self, database: Database, registry: ToolRegistry, settings: Settings
    ) -> None:
        self.database = database
        self.registry = registry
        self.settings = settings
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.llm_slots = asyncio.Semaphore(1)
        self.local_slots = asyncio.Semaphore(2)
        self.llm_tools = {
            "synthetic_generate_cot",
            "easy_dataset_generate",
            "easy_dataset_workflow",
            "kaqg_build_graph",
            "kaqg_generate_evaluate",
        }

    def start(self, run_id: str) -> None:
        self.cancel_events[run_id] = asyncio.Event()
        self.tasks[run_id] = asyncio.create_task(self._execute(run_id))

    async def cancel(self, run_id: str) -> bool:
        event = self.cancel_events.get(run_id)
        task = self.tasks.get(run_id)
        run = self.database.get_run(run_id)
        if not run or run["status"] in {"succeeded", "failed", "cancelled"}:
            return False
        if event:
            event.set()
        if task:
            task.cancel()
        self.database.append_event(run_id, "用户请求取消运行", "warning")
        self.database.update_run(
            run_id, status="cancelled", completed_at=now_iso(), error="用户取消"
        )
        return True

    async def emit(
        self, run_id: str, log_path: Path, message: str, event_type: str, data: dict
    ) -> None:
        safe_message = redact_secrets(message)
        safe_data = json.loads(redact_secrets(json.dumps(data, ensure_ascii=False)))
        self.database.append_event(run_id, safe_message, event_type, safe_data)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": now_iso(),
                        "type": event_type,
                        "message": safe_message,
                        "data": safe_data,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    async def _execute(self, run_id: str) -> None:
        run = self.database.get_run(run_id)
        if not run:
            return
        tool = self.registry.get(run["tool_name"])
        if not tool:
            self.database.update_run(
                run_id,
                status="failed",
                completed_at=now_iso(),
                error="工具未注册",
            )
            return
        run_dir = self.settings.runtime_root / "runs" / run["project_id"] / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "events.jsonl"
        cancel_event = self.cancel_events[run_id]

        async def emit_fn(
            message: str, event_type: str = "log", data: dict | None = None
        ) -> None:
            event_data = data or {}
            stage_id = event_data.get("stage_id")
            if event_type == "stage" and stage_id:
                self.database.set_run_stage(
                    run_id, str(stage_id), progress=event_data.get("progress")
                )
            elif event_type == "metric" and event_data.get("progress"):
                self.database.update_run(
                    run_id, progress_json=event_data["progress"]
                )
            await self.emit(run_id, log_path, message, event_type, event_data)

        context = ExecutionContext(
            session_id=run_id,
            task_node_id=run_id,
            working_directory=str(run_dir),
            project_id=run["project_id"],
            run_id=run_id,
            emit_fn=emit_fn,
            cancel_event=cancel_event,
        )
        semaphore = (
            self.llm_slots if tool.name in self.llm_tools else self.local_slots
        )
        self.database.update_run(
            run_id,
            status="running",
            started_at=now_iso(),
            log_path=str(log_path),
        )
        await emit_fn(f"开始执行 {tool.name}", "stage", {"risk": tool.risk_level.value})
        try:
            async with semaphore:
                result = await asyncio.wait_for(
                    tool.execute(run["input"], context),
                    timeout=tool.timeout_ms / 1000,
                )
            payload = result.model_dump(mode="json")
            samples = []
            if isinstance(result.data, dict):
                samples = result.data.pop("samples", []) or []
                payload["data"] = result.data
            config_hash = hashlib.sha256(
                json.dumps(run["input"], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]
            for sample in samples:
                generation = sample.setdefault("generation", {})
                generation["config_hash"] = config_hash
            if samples:
                self.database.insert_samples(
                    run["project_id"], run_id, samples
                )
            status = "succeeded" if result.success else "failed"
            self.database.update_run(
                run_id,
                status=status,
                result_json=payload,
                error=result.error.message if result.error else None,
                artifact_path=result.persisted_path,
                completed_at=now_iso(),
            )
            self.database.finish_run_stages(run_id, result.success)
            await emit_fn(
                result.summary,
                "completed" if result.success else "error",
                {"sample_count": len(samples), "artifact": result.persisted_path},
            )
        except asyncio.CancelledError:
            self.database.update_run(
                run_id,
                status="cancelled",
                error="用户取消",
                completed_at=now_iso(),
            )
            self.database.finish_run_stages(run_id, False)
        except TimeoutError:
            error = ToolError(
                code="run_timeout", message=f"运行超过 {tool.timeout_ms / 1000:.0f} 秒"
            )
            result = ToolResult(success=False, summary="运行超时", error=error)
            self.database.update_run(
                run_id,
                status="failed",
                result_json=result.model_dump(mode="json"),
                error=error.message,
                completed_at=now_iso(),
            )
            self.database.finish_run_stages(run_id, False)
            await emit_fn(error.message, "error")
        except Exception as exc:
            safe_error = redact_secrets(str(exc))
            self.database.update_run(
                run_id,
                status="failed",
                error=safe_error,
                completed_at=now_iso(),
            )
            await emit_fn(
                f"运行异常：{safe_error}",
                "error",
                {"trace": redact_secrets(traceback.format_exc())[-4000:]},
            )
        finally:
            self.tasks.pop(run_id, None)
            self.cancel_events.pop(run_id, None)
