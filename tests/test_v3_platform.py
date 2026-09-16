from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

import app.main as main
from app.database import Database


client = TestClient(main.app)


@pytest.fixture(scope="module", autouse=True)
def isolated_database(tmp_path_factory: pytest.TempPathFactory):
    original = main.database
    isolated = Database(Path(tmp_path_factory.mktemp("v3-db")) / "workbench.sqlite3")
    main.database = isolated
    main.manager.database = isolated
    main.medical_manager.database = isolated
    yield
    main.database = original
    main.manager.database = original
    main.medical_manager.database = original


def medical_project() -> dict:
    response = client.post(
        "/api/v2/tools/medical/projects",
        json={"name": f"medical-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_v3_exposes_modules_operators_and_templates():
    modules = client.get("/api/v3/modules")
    assert modules.status_code == 200
    assert {item["id"] for item in modules.json()} == {"toolbox", "domain", "assets"}
    operators = client.get("/api/v3/operators")
    assert operators.status_code == 200
    assert any(item["type"] == "source.synthea" for item in operators.json())
    templates = client.get("/api/v3/pipelines/templates")
    assert templates.status_code == 200
    assert any(item["id"] == "medical-synthea" for item in templates.json())


def test_pipeline_rejects_cycle_and_accepts_medical_template():
    project = medical_project()
    cyclic = client.post(
        "/api/v3/pipelines",
        json={
            "name": f"bad-{uuid.uuid4()}", "domain": "medical", "project_id": project["id"],
            "nodes": [
                {"id": "source", "type": "source.synthea", "label": "源"},
                {"id": "output", "type": "output.medical-dataset", "label": "输出"},
            ],
            "edges": [{"source": "source", "target": "output"}, {"source": "output", "target": "source"}],
        },
    )
    assert cyclic.status_code == 422

    template = next(item for item in client.get("/api/v3/pipelines/templates").json() if item["id"] == "medical-synthea")
    created = client.post(
        "/api/v3/pipelines",
        json={
            "name": f"medical-flow-{uuid.uuid4()}", "description": "test", "domain": "medical",
            "project_id": project["id"], "nodes": template["nodes"], "edges": template["edges"],
        },
    )
    assert created.status_code == 201, created.text
    validated = client.post(f"/api/v3/pipelines/{created.json()['id']}/validate")
    assert validated.status_code == 200
    assert validated.json()["valid"] is True


def test_dataset_version_cannot_publish_before_quality_gate():
    record = main.database.create_dataset_version(
        name=f"quality-{uuid.uuid4()}", domain="medical", project_id=None,
        manifest={"synthetic": True}, quality={"passed": False}, artifact_path=None,
    )
    response = client.post(f"/api/v3/datasets/{record['dataset_id']}/versions/{record['id']}/publish")
    assert response.status_code == 409
    assert "质量门禁" in response.json()["detail"]


def test_medical_output_catalog_exposes_dependencies_and_three_modes():
    response = client.get("/api/v3/medical/output-catalog")
    assert response.status_code == 200
    catalog = {item["id"]: item for item in response.json()}
    assert catalog["csv:encounters"]["depends_on"] == ["csv:patients"]
    assert set(catalog["derived:quality-report"]["modes"]) == {"omit", "internal", "publish"}
