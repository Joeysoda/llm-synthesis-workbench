from __future__ import annotations

from dataclasses import replace

import pytest

import app.main as main


@pytest.fixture(autouse=True)
def isolate_workbench(tmp_path):
    """Keep every API test away from the developer's real workbench database."""
    original_database = main.database
    original_database_path = original_database.path
    original_settings = main.settings
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    isolated_path = runtime_root / "workbench.sqlite3"

    original_database.path = isolated_path
    original_database.initialize()
    isolated_settings = replace(
        original_settings,
        runtime_root=runtime_root,
        database_path=isolated_path,
    )
    main.settings = isolated_settings
    main.manager.database = original_database
    main.manager.settings = isolated_settings
    main.medical_manager.database = original_database
    main.medical_manager.settings = isolated_settings
    try:
        yield
    finally:
        main.database = original_database
        main.settings = original_settings
        original_database.path = original_database_path
        main.manager.database = original_database
        main.manager.settings = original_settings
        main.medical_manager.database = original_database
        main.medical_manager.settings = original_settings
