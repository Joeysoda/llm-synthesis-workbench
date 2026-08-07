from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.cleanlab import build_cleanlab_tools
from app.config import settings
from app.contracts import ExecutionContext
from app.database import Database, now_iso
from app.main import app, database, manager


client = TestClient(app)
FIXTURES = Path(__file__).resolve().parents[1] / "demo-inputs"


def _project(tool_id: str) -> dict:
    response = client.post(
        f"/api/v2/tools/{tool_id}/projects",
        json={"name": f"quality-{tool_id}-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(project_id: str, path: Path) -> dict:
    response = client.post(
        f"/api/v2/projects/{project_id}/assets",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_kaqg_job_only_accepts_pdf_and_has_six_stages(monkeypatch):
    project = _project("kaqg")
    invalid = _upload(project["id"], FIXTURES / "kaqg" / "数据平台运维与质量规范.md")
    rejected = client.post(
        f"/api/v2/kaqg/projects/{project['id']}/jobs",
        json={
            "asset_id": invalid["id"],
            "subject_name": "数据平台运维规范",
            "difficulty_counts": {"easy": 1, "medium": 1, "hard": 1},
            "confirmed": True,
        },
    )
    assert rejected.status_code == 422

    pdf = _upload(project["id"], FIXTURES / "kaqg" / "数据平台运维与质量规范.pdf")
    monkeypatch.setattr(manager, "start", lambda _run_id: None)
    accepted = client.post(
        f"/api/v2/kaqg/projects/{project['id']}/jobs",
        json={
            "asset_id": pdf["id"],
            "subject_name": "数据平台运维规范",
            "difficulty_counts": {"easy": 1, "medium": 1, "hard": 1},
            "confirmed": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert [stage["id"] for stage in accepted.json()["stages"]] == [
        "check", "parse", "graph", "generate", "evaluate", "export"
    ]


def test_cleanlab_fixture_meets_detection_acceptance(tmp_path):
    runtime = tmp_path / "runtime"
    for name in ("uploads", "runs", "logs", "exports"):
        (runtime / name).mkdir(parents=True)
    test_database = Database(runtime / "test.sqlite3")
    project = test_database.create_tool_project("cleanlab", "fixture")
    source = FIXTURES / "cleanlab" / "数据质量工单.csv"
    target = runtime / "uploads" / source.name
    shutil.copyfile(source, target)
    test_database.create_asset(
        {
            "id": "fixture-asset",
            "project_id": project["id"],
            "filename": source.name,
            "path": str(target),
            "content_type": "text/csv",
            "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "created_at": now_iso(),
        }
    )
    run_dir = runtime / "runs" / project["id"] / "fixture-run"
    run_dir.mkdir(parents=True)
    context = ExecutionContext(
        session_id="pytest",
        task_node_id="cleanlab",
        working_directory=str(run_dir),
        project_id=project["id"],
        run_id="fixture-run",
    )
    tool = build_cleanlab_tools(
        test_database,
        replace(settings, runtime_root=runtime, database_path=runtime / "test.sqlite3"),
    )[0]
    result = asyncio.run(
        tool.execute(
            {"source_type": "asset", "asset_id": "fixture-asset"},
            context,
        )
    )
    assert result.success, result.error

    expected = json.loads(
        (FIXTURES / "cleanlab" / "已知问题清单.json").read_text(encoding="utf-8")
    )["expected_issues"]
    wrong_labels = {item["id"] for item in expected["wrong_label"]}
    duplicates = {item["id"] for item in expected["duplicate_or_near_duplicate"]}
    outliers = {item["id"] for item in expected["outlier"]}
    samples = result.data["samples"]
    top_labels = {
        item["source"]["row_id"]
        for item in sorted(samples, key=lambda row: row["quality"]["label_score"])[:12]
    }
    top_outliers = {
        item["source"]["row_id"]
        for item in sorted(samples, key=lambda row: row["quality"]["outlier_score"])[:8]
    }
    detected_duplicates = {
        item["source"]["row_id"]
        for item in samples
        if item["quality"]["is_near_duplicate_issue"]
    }
    assert len(wrong_labels & top_labels) >= 6
    assert len(outliers & top_outliers) >= 3
    assert duplicates <= detected_duplicates


def test_cleanlab_review_preserves_original_label():
    project = _project("cleanlab")
    run = database.create_run(
        project["id"],
        "cleanlab_audit_dataset",
        {},
        workflow_type="text-classification-audit",
    )
    sample_id = f"{run['id']}/1"
    database.insert_samples(
        project["id"],
        run["id"],
        [
            {
                "id": sample_id,
                "task_type": "data_quality_audit",
                "question": "示例文本",
                "answer": "原标签",
                "reasoning": "建议复核",
                "source": {"row_id": "T001", "original_label": "原标签"},
                "generation": {"tool": "cleanlab"},
                "quality": {"suggested_label": "建议标签", "decision": "pending"},
            }
        ],
    )
    accepted = client.patch(
        f"/api/v2/cleanlab/samples/{run['id']}/1/decision",
        json={"decision": "accept_suggestion"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["answer"] == "建议标签"
    assert accepted.json()["source"]["original_label"] == "原标签"

    manual = client.patch(
        f"/api/v2/cleanlab/samples/{run['id']}/1/decision",
        json={"decision": "manual", "corrected_label": "人工标签"},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["answer"] == "人工标签"
    assert manual.json()["source"]["original_label"] == "原标签"
