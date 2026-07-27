from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_project_upload_and_confirmation_gate():
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["local_only"] is True

    project = client.post(
        "/api/v1/projects",
        json={"name": f"pytest-{uuid.uuid4()}", "description": "integration"},
    )
    assert project.status_code == 201
    project_id = project.json()["id"]

    upload = client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("fixture.md", "# 测试\n\n这是脱敏材料。", "text/markdown")},
    )
    assert upload.status_code == 201
    assert upload.json()["size"] > 0

    blocked = client.post(
        "/api/v1/runs",
        json={
            "project_id": project_id,
            "tool_name": "synlogic_generate_arrow_maze",
            "input": {"num_of_data": 1},
            "confirmed": False,
        },
    )
    assert blocked.status_code == 409


def test_tools_expose_eight_atomic_definitions():
    response = client.get("/api/v1/tools")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert names == {
        "synthetic_ingest",
        "synthetic_generate_cot",
        "synthetic_curate_export",
        "easy_dataset_ingest",
        "easy_dataset_generate",
        "easy_dataset_export",
        "synlogic_generate_arrow_maze",
        "synlogic_verify_arrow_maze",
    }
