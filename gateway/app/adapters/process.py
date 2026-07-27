from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..contracts import ExecutionContext
from ..security import redact_secrets


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    output: str


async def run_command(
    command: Iterable[str | Path],
    *,
    cwd: Path,
    ctx: ExecutionContext,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 300,
) -> CommandResult:
    args = [str(item) for item in command]
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    await ctx.emit(
        f"启动阶段：{Path(args[0]).name} {' '.join(args[1:3])}",
        "stage",
        {"command": [Path(args[0]).name, *args[1:]]},
    )

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=process_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    lines: list[str] = []

    async def pump() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            text = redact_secrets(line.decode("utf-8", errors="replace").rstrip())
            lines.append(text)
            await ctx.emit(text)

    pump_task = asyncio.create_task(pump())
    wait_task = asyncio.create_task(process.wait())
    cancel_task = (
        asyncio.create_task(ctx.cancel_event.wait()) if ctx.cancel_event else None
    )
    try:
        waits = [wait_task]
        if cancel_task:
            waits.append(cancel_task)
        done, _ = await asyncio.wait(
            waits,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task and cancel_task in done and ctx.cancelled():
            process.terminate()
            await process.wait()
            raise asyncio.CancelledError
        if wait_task not in done:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
            raise TimeoutError(f"子进程超过 {timeout_seconds:.0f} 秒")
        returncode = wait_task.result()
        await pump_task
        return CommandResult(args, returncode, "\n".join(lines))
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        raise
    finally:
        if cancel_task:
            cancel_task.cancel()
        if not pump_task.done():
            pump_task.cancel()


def command_failed(result: CommandResult) -> bool:
    text = result.output.lower()
    failure_markers = (
        "unauthorized",
        "authenticationerror",
        "invalid api key",
        "api key not found",
        "❌ error",
        "traceback (most recent call last)",
    )
    return result.returncode != 0 or any(marker in text for marker in failure_markers)
