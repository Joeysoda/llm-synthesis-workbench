from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.adapters.easy_dataset import EVAL_TYPE_MAP, _model_reference
from app.adapters.synthetic import cot_enhance_records
from app.main import app, manager


client = TestClient(app)
created_project_ids: list[str] = []


def test_easy_dataset_model_reference_keeps_loopback_proxy_fields():
    reference = _model_reference(
        {
            "id": "model-config-id",
            "providerId": "custom",
            "endpoint": "http://127.0.0.1:18000/internal/llm/v1",
            "apiKey": "local-gateway-proxy",
            "modelId": "MiniMax-M3",
            "modelName": "MiniMax-M3",
            "type": "text",
        },
        "MiniMax-M3",
        "text",
    )
    assert reference["endpoint"].startswith("http://127.0.0.1:18000/")
    assert reference["apiKey"] == "local-gateway-proxy"
    assert reference["modelId"] == "MiniMax-M3"


def test_eval_type_map_covers_every_page_question_type():
    assert EVAL_TYPE_MAP == {
        "true-false": "true_false",
        "single-choice": "single_choice",
        "multiple-choice": "multiple_choice",
        "short-answer": "short_answer",
        "open": "open_ended",
    }


def test_cot_enhance_records_preserves_qa_and_conversation_content():
    qa_records = cot_enhance_records(
        {"qa_pairs": [{"question": "问题", "answer": "答案"}]}
    )
    conversation_records = cot_enhance_records(
        {
            "conversations": [
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "带推理的回答"},
            ]
        }
    )
    assert qa_records == [{"question": "问题", "answer": "答案", "reasoning": ""}]
    assert conversation_records == [
        {"question": "问题", "answer": "带推理的回答", "reasoning": ""}
    ]


@pytest.fixture(autouse=True)
def archive_created_projects():
    start = len(created_project_ids)
    yield
    for project_id in created_project_ids[start:]:
        response = client.patch(
            f"/api/v2/projects/{project_id}",
            json={"archived": True},
        )
        assert response.status_code == 200, response.text


def create(tool_id: str, name: str):
    response = client.post(
        f"/api/v2/tools/{tool_id}/projects",
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    created_project_ids.append(project["id"])
    return project


def test_projects_are_isolated_by_tool_and_can_share_display_name():
    name = f"same-name-{uuid.uuid4()}"
    synthetic = create("synthetic-data-kit", name)
    easy = create("easy-dataset", name)
    assert synthetic["display_name"] == easy["display_name"] == name
    assert synthetic["tool_id"] == "synthetic-data-kit"
    assert easy["tool_id"] == "easy-dataset"


def test_job_rejects_project_from_another_tool():
    project = create("easy-dataset", f"mismatch-{uuid.uuid4()}")
    response = client.post(
        f"/api/v2/synlogic/projects/{project['id']}/jobs",
        json={"workflow_type": "arrow-maze", "confirmed": True},
    )
    assert response.status_code == 409
    assert "属于 Easy Dataset" in response.json()["detail"]


def test_synlogic_page_job_has_flat_stage_rows():
    project = create("synlogic", f"stages-{uuid.uuid4()}")
    response = client.post(
        f"/api/v2/synlogic/projects/{project['id']}/jobs",
        json={
            "workflow_type": "arrow-maze",
            "num_of_data": 1,
            "confirmed": True,
        },
    )
    assert response.status_code == 202
    assert [stage["id"] for stage in response.json()["stages"]] == [
        "generate",
        "verify",
        "export",
    ]


def upload(project_id: str, filename: str, content: bytes):
    response = client.post(
        f"/api/v2/projects/{project_id}/assets",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_cot_enhance_rejects_document_before_creating_job():
    project = create("synthetic-data-kit", f"cot-doc-{uuid.uuid4()}")
    asset = upload(project["id"], "material.docx", b"not-a-json-document")
    response = client.post(
        f"/api/v2/synthetic/projects/{project['id']}/jobs",
        json={
            "workflow_type": "cot-enhance",
            "asset_ids": [asset["id"]],
            "num_pairs": 1,
            "confirmed": True,
        },
    )
    assert response.status_code == 422
    assert "普通 DOCX/PDF 请改选“CoT 思维链”" in response.json()["detail"]


def test_cot_enhance_rejects_json_without_conversations():
    project = create("synthetic-data-kit", f"cot-invalid-{uuid.uuid4()}")
    asset = upload(project["id"], "invalid.json", b'{"items": [{"text": "hello"}]}')
    response = client.post(
        f"/api/v2/synthetic/projects/{project['id']}/jobs",
        json={
            "workflow_type": "cot-enhance",
            "asset_ids": [asset["id"]],
            "num_pairs": 1,
            "confirmed": True,
        },
    )
    assert response.status_code == 422
    assert "没有可增强的问答" in response.json()["detail"]


def test_cot_enhance_accepts_qa_pairs(monkeypatch):
    project = create("synthetic-data-kit", f"cot-valid-{uuid.uuid4()}")
    asset = upload(
        project["id"],
        "valid.json",
        (
            '{"qa_pairs":[{"question":"为什么要清洗数据？",'
            '"answer":"为了减少错误和噪声。"}]}'
        ).encode(),
    )
    monkeypatch.setattr(manager, "start", lambda _run_id: None)
    response = client.post(
        f"/api/v2/synthetic/projects/{project['id']}/jobs",
        json={
            "workflow_type": "cot-enhance",
            "asset_ids": [asset["id"]],
            "num_pairs": 1,
            "min_retained": 1,
            "confirmed": True,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["workflow_type"] == "cot-enhance"


def test_probe_does_not_send_request_when_rotation_is_unconfirmed(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-redacted")
    monkeypatch.setenv("LLM_CREDENTIAL_ROTATED", "false")
    response = client.post("/api/v2/integrations/text/probe", json={})
    assert response.status_code == 200
    assert response.json()["code"] == "rotation-unconfirmed"
    assert "未发送外部请求" in response.json()["message"]
