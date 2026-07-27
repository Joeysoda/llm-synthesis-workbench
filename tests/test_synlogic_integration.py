from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.adapters.synlogic import build_synlogic_tools
from app.config import settings
from app.database import Database
from app.registry import ToolRegistry
from app.runner import RunManager


@pytest.mark.asyncio
async def test_real_synlogic_generator_and_verifier(tmp_path):
    local = replace(
        settings,
        runtime_root=tmp_path / "runtime",
        database_path=tmp_path / "runtime" / "test.sqlite3",
    )
    for name in ("runs", "uploads", "logs", "exports"):
        (local.runtime_root / name).mkdir(parents=True, exist_ok=True)
    database = Database(local.database_path)
    project = database.create_project("synlogic-test")
    registry = ToolRegistry()
    registry.register_many(build_synlogic_tools(database, local))
    manager = RunManager(database, registry, local)
    run = database.create_run(
        project["id"],
        "synlogic_generate_arrow_maze",
        {
            "num_of_data": 20,
            "width": 5,
            "height": 5,
            "arrow_fill_rate_min": 0.3,
            "arrow_fill_rate_max": 0.9,
        },
    )
    manager.start(run["id"])
    await manager.tasks[run["id"]]
    finished = database.get_run(run["id"])
    assert finished["status"] == "succeeded"
    assert len(database.list_samples(run["id"])) == 20
    assert finished["result"]["data"]["verified"] == 20
    assert finished["result"]["data"]["negative_rejected"] is True
