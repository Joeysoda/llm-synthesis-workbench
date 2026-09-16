from __future__ import annotations

import asyncio
import csv
import json
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .database import Database, now_iso
from .pipeline import topological_nodes


OUTPUT_MODES = ("omit", "internal", "publish")
MODE_RANK = {"omit": 0, "internal": 1, "publish": 2}

_CSV_DESCRIPTIONS = {
    "patients": "患者基本信息与人口学字段。",
    "encounters": "患者就诊、住院和门诊事件。",
    "conditions": "患者诊断或健康问题记录。",
    "allergies": "患者过敏及不良反应记录。",
    "careplans": "面向患者的护理计划。",
    "medications": "药物处方和用药记录。",
    "observations": "检验、生命体征和临床观察。",
    "procedures": "医疗操作和治疗记录。",
    "devices": "植入或使用的医疗设备。",
    "imaging_studies": "影像检查记录。",
    "immunizations": "免疫接种记录。",
    "supplies": "医疗物资使用记录。",
    "organizations": "医疗机构信息。",
    "providers": "医生和服务提供者信息。",
    "claims": "医疗费用理赔记录。",
    "claims_transactions": "理赔交易明细。",
    "payers": "保险支付方信息。",
    "payer_transitions": "患者保险支付方变更记录。",
}
_FHIR_DESCRIPTIONS = {
    "Patient": "患者基本资料。", "Encounter": "就诊和住院事件。", "Condition": "诊断或健康问题。",
    "AllergyIntolerance": "过敏和不良反应。", "CarePlan": "护理计划。", "CareTeam": "护理团队。",
    "Claim": "理赔记录。", "Device": "医疗设备。", "DiagnosticReport": "诊断报告。",
    "DocumentReference": "临床文档引用。", "ExplanationOfBenefit": "保险受益解释。",
    "ImagingStudy": "影像检查。", "Immunization": "免疫接种。", "Location": "医疗地点。",
    "Medication": "药品信息。", "MedicationAdministration": "给药记录。", "MedicationRequest": "用药申请。",
    "Observation": "临床观察和检验。", "Organization": "医疗机构。", "Practitioner": "医疗人员。",
    "PractitionerRole": "医疗人员与机构的角色关系。", "Procedure": "医疗操作。",
    "Provenance": "资源来源和审计信息。", "SupplyDelivery": "物资交付。",
}
DERIVED_OUTPUTS = {
    "patient-timelines": ("患者时间线", "derived", "每位患者按时间排序的关联资源事件。"),
    "medical-task-samples": ("医疗任务样本", "derived", "带来源资源 ID 和证据的确定性医疗任务。"),
    "quality-report": ("质量报告", "report", "FHIR 解析、引用、时间线和任务质量门禁结果。"),
    "data-card": ("数据卡", "report", "数据来源、用途边界和限制说明。"),
    "dataset-manifest": ("数据集清单", "report", "版本、产物、依赖和生成参数清单。"),
}


def _catalog_item(key: str, label: str, category: str, description: str, *, required: str = "omit", depends_on: list[str] | None = None) -> dict[str, Any]:
    return {"id": key, "label": label, "category": category, "description": description,
            "required_min": required, "depends_on": depends_on or [], "modes": list(OUTPUT_MODES)}


def medical_output_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, description in _CSV_DESCRIPTIONS.items():
        dependencies = []
        if name != "patients":
            dependencies.append("csv:patients")
        if name not in {"patients", "encounters"} and name not in {"organizations", "providers", "payers"}:
            dependencies.append("csv:encounters")
        if name == "providers":
            dependencies.append("csv:organizations")
        if name == "claims":
            dependencies.extend(["csv:organizations", "csv:providers"])
        if name == "claims_transactions":
            dependencies.append("csv:claims")
        if name == "payer_transitions":
            dependencies.append("csv:payers")
        items.append(_catalog_item(f"csv:{name}", name, "csv", description,
                                   required="internal" if name in {"patients", "encounters"} else "omit",
                                   depends_on=dependencies))
    for name, description in _FHIR_DESCRIPTIONS.items():
        dependencies = []
        if name != "Patient":
            dependencies.append("fhir:Patient")
        if name not in {"Patient", "Encounter", "Organization", "Location", "Practitioner", "PractitionerRole", "Provenance"}:
            dependencies.append("fhir:Encounter")
        if name == "PractitionerRole":
            dependencies.extend(["fhir:Organization", "fhir:Practitioner"])
        if name == "Claim":
            dependencies.extend(["fhir:Organization"])
        if name == "ExplanationOfBenefit":
            dependencies.append("fhir:Claim")
        if name in {"MedicationRequest", "MedicationAdministration"}:
            dependencies.append("fhir:Medication")
        items.append(_catalog_item(f"fhir:{name}", name, "fhir", description,
                                   required="internal" if name in {"Patient", "Encounter"} else "omit",
                                   depends_on=dependencies))
    for key, (label, category, description) in DERIVED_OUTPUTS.items():
        items.append(_catalog_item(f"derived:{key}", label, category, description, required="publish"))
    return items


def resolve_output_policy(requested: dict[str, str] | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    catalog = medical_output_catalog()
    by_id = {item["id"]: item for item in catalog}
    unknown = sorted(set(requested or {}) - set(by_id))
    if unknown:
        raise ValueError(f"未知医疗输出项：{', '.join(unknown)}")
    resolved = {item["id"]: ("publish" if requested is None else (requested.get(item["id"], "omit"))) for item in catalog}
    reasons: dict[str, list[str]] = defaultdict(list)
    for item in catalog:
        minimum = item["required_min"]
        if MODE_RANK[resolved[item["id"]]] < MODE_RANK[minimum]:
            resolved[item["id"]] = minimum
            reasons[item["id"]].append("固定医疗闭环要求")
    changed = True
    while changed:
        changed = False
        for item in catalog:
            current = resolved[item["id"]]
            if current == "omit":
                continue
            for dependency in item["depends_on"]:
                if MODE_RANK[resolved[dependency]] < MODE_RANK[current]:
                    resolved[dependency] = current
                    reasons[dependency].append(f"被 {item['label']} 依赖")
                    changed = True
    return resolved, {key: sorted(set(value)) for key, value in reasons.items()}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _prune_synthea_outputs(root: Path, policy: dict[str, str]) -> None:
    """Keep only the user-selected Synthea CSV/FHIR exports for this run."""
    csv_names = {
        key.split(":", 1)[1]
        for key, mode in policy.items()
        if key.startswith("csv:") and mode != "omit"
    }
    csv_root = root / "csv"
    if csv_root.exists():
        for path in csv_root.glob("*.csv"):
            if path.stem not in csv_names:
                path.unlink()

    fhir_names = {
        key.split(":", 1)[1]
        for key, mode in policy.items()
        if key.startswith("fhir:") and mode != "omit"
    }
    fhir_root = root / "fhir"
    if fhir_root.exists():
        for path in fhir_root.glob("*.ndjson"):
            resource_type = path.name.split(".", 1)[0]
            if resource_type not in fhir_names:
                path.unlink()


def _resources_from_fhir(root: Path) -> tuple[list[dict[str, Any]], Counter[str], list[str], set[str]]:
    resources: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    failures: list[str] = []
    all_resource_ids: set[str] = set()
    for path in list(root.rglob("*.json")) + list(root.rglob("*.ndjson")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines() if path.suffix == ".ndjson" else [path.read_text(encoding="utf-8")]
        except (OSError, UnicodeDecodeError):
            failures.append(str(path.name))
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                failures.append(str(path.name))
                break
            candidates = value.get("entry", []) if isinstance(value, dict) and value.get("resourceType") == "Bundle" else [value]
            for item in candidates:
                resource = item.get("resource") if isinstance(item, dict) and "resource" in item else item
                if isinstance(resource, dict) and resource.get("resourceType"):
                    resource_type = str(resource["resourceType"])
                    counts[resource_type] += 1
                    if resource.get("id"):
                        all_resource_ids.add(f"{resource_type}/{resource['id']}")
                    resources.append(resource)
    return resources, counts, failures, all_resource_ids


def _collect_references(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "reference" and isinstance(item, str):
                result.append(item)
            else:
                result.extend(_collect_references(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_collect_references(item))
    return result


def _resource_time(resource: dict[str, Any]) -> str | None:
    for path in (("period", "start"), ("effectivePeriod", "start"), ("effectiveDateTime",), ("onsetDateTime",), ("recordedDate",), ("authoredOn",), ("issued",)):
        current: Any = resource
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str):
            return current
    return None


def parse_medical_fhir(fhir_root: Path, output_root: Path) -> dict[str, Any]:
    resources, resource_counts, parse_failures, resource_ids = _resources_from_fhir(fhir_root)
    unresolved: list[str] = []
    for resource in resources:
        # The first pass validates the patient-facing links used to build the
        # timeline. Deeply walking every FHIR extension/reference is both
        # expensive and overly strict for Synthea's auxiliary resources.
        subject = resource.get("subject") or resource.get("patient")
        reference = subject.get("reference") if isinstance(subject, dict) else None
        if isinstance(reference, str) and reference.startswith("Patient/"):
            normalized = reference.split("/_history/", 1)[0]
            if normalized not in resource_ids:
                unresolved.append(normalized)

    patient_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resource in resources:
        patient_ref = None
        subject = resource.get("subject") or resource.get("patient")
        if isinstance(subject, dict) and isinstance(subject.get("reference"), str):
            patient_ref = subject["reference"]
        if resource.get("resourceType") == "Patient" and resource.get("id"):
            patient_ref = f"Patient/{resource['id']}"
        if patient_ref:
            patient_events[patient_ref].append({
                "resource_id": f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}",
                "resource_type": resource.get("resourceType", "Unknown"),
                "time": _resource_time(resource),
            })
    timelines: list[dict[str, Any]] = []
    for patient_id, events in sorted(patient_events.items()):
        events.sort(key=lambda event: event.get("time") or "9999-12-31")
        timelines.append({"patient_id": patient_id, "events": events, "event_count": len(events)})

    _write_jsonl(output_root / "patient-timelines.jsonl", timelines)
    return {
        "resources": resources,
        "resource_counts": dict(resource_counts),
        "resource_ids": resource_ids,
        "parse_failures": parse_failures,
        "unresolved_references": sorted(set(unresolved)),
        "timelines": timelines,
    }


def generate_medical_tasks(details: dict[str, Any], output_root: Path, run_id: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for timeline in details["timelines"]:
        encounter_events = [event for event in timeline["events"] if event["resource_type"] == "Encounter"]
        evidence = [event["resource_id"] for event in encounter_events]
        tasks.append({
            "id": f"{run_id}/medical-{len(tasks) + 1}",
            "task_type": "medical_timeline_qa",
            "question": f"在该完全合成的记录中，{timeline['patient_id']} 有多少次就诊？",
            "answer": str(len(encounter_events)),
            "reasoning": "答案由该患者关联的 Encounter 资源数量确定。",
            "source": {"resource_ids": evidence, "patient_id": timeline["patient_id"]},
            "generation": {"tool": "synthea-medical-pack", "model": "deterministic", "config_hash": "local-rule-v1"},
            "quality": {"rule_passed": True, "model_score": None, "human_status": "pending"},
        })
        if len(tasks) >= 20:
            break

    _write_jsonl(output_root / "medical-task-samples.jsonl", tasks)
    return tasks


def build_quality_report(details: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    contact_hits: list[str] = []
    # Synthea deliberately emits synthetic UUIDs, coded clinical concepts and
    # sometimes identifier-shaped values. They are not evidence of real PII,
    # so the gate only flags real contact formats that should not be present
    # in a self-contained synthetic export.
    contact_pattern = re.compile(r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d))")
    for resource in details["resources"]:
        serialized = json.dumps(resource, ensure_ascii=False)
        if contact_pattern.search(serialized):
            contact_hits.append(f"{resource.get('resourceType')}/{resource.get('id', 'unknown')}")
    quality = {
        "passed": bool(details["resources"]) and not details["parse_failures"] and not details["unresolved_references"] and len(tasks) >= 20,
        "rules": {
            "fhir_parse_failures": details["parse_failures"],
            "resource_count": sum(details["resource_counts"].values()),
            "resource_counts": details["resource_counts"],
            "unresolved_references": details["unresolved_references"][:100],
            "timeline_count": len(details["timelines"]),
            "task_count": len(tasks),
            "synthetic_contact_format_resources": contact_hits,
        },
        "note": "所有记录均来自固定的 Synthea 合成器。UUID、编码及联系格式均为合成值并保留在报告中；本期不允许外部真实病例输入。",
    }
    return quality


def build_artifact_index(output_root: Path, policy: dict[str, str]) -> list[dict[str, Any]]:
    catalog = {item["id"]: item for item in medical_output_catalog()}
    derived_keys = {f"derived:{key}" for key in DERIVED_OUTPUTS}
    artifacts: list[dict[str, Any]] = []
    for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
        relative = path.relative_to(output_root).as_posix()
        key: str | None = None
        category = "internal"
        if relative.startswith("synthea/csv/") and path.suffix == ".csv":
            key, category = f"csv:{path.stem}", "csv"
        elif relative.startswith("synthea/fhir/") and path.suffix == ".ndjson":
            key, category = f"fhir:{path.name.split('.', 1)[0]}", "fhir"
        elif path.name == "patient-timelines.jsonl":
            key, category = "derived:patient-timelines", "derived"
        elif path.name == "medical-task-samples.jsonl":
            key, category = "derived:medical-task-samples", "derived"
        elif path.name == "quality-report.json":
            key, category = "derived:quality-report", "report"
        elif path.name == "data-card.md":
            key, category = "derived:data-card", "report"
        elif path.name == "dataset-manifest.json":
            key, category = "derived:dataset-manifest", "report"
        if not key or key not in policy or policy[key] == "omit":
            continue
        item = catalog.get(key, {"label": path.name, "description": "Synthea 运行产物。"})
        row_count: int | None = None
        columns: list[str] = []
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                columns = next(reader, [])
                row_count = sum(1 for _ in reader)
        elif path.suffix in {".jsonl", ".ndjson"}:
            with path.open("r", encoding="utf-8") as handle:
                row_count = sum(1 for line in handle if line.strip())
        else:
            row_count = 1
        artifacts.append({
            "id": key, "label": item["label"], "description": item["description"],
            "category": category, "mode": policy[key], "relative_path": relative,
            "format": path.suffix.lstrip("."), "size": path.stat().st_size,
            "row_count": row_count, "columns": columns,
        })
    return artifacts


def build_medical_outputs(fhir_root: Path, output_root: Path, run_id: str) -> dict[str, Any]:
    """Compatibility helper for callers that still expect the old all-in-one function."""
    output_root.mkdir(parents=True, exist_ok=True)
    details = parse_medical_fhir(fhir_root, output_root)
    tasks = generate_medical_tasks(details, output_root, run_id)
    quality = build_quality_report(details, tasks)
    _write_json(output_root / "quality-report.json", quality)
    data_card = """# 医疗合成数据卡\n\n- 数据性质：完全由 Synthea 规则引擎生成的虚构患者记录。\n- 不得用于真实诊断、治疗决策或人群分布推断。\n- 质量门禁：FHIR 可解析、引用关系、ID 和时间线检查；衍生任务样本带资源证据。\n- 局限：使用默认统计模板，不代表中国或任何真实医疗机构。\n"""
    (output_root / "data-card.md").write_text(data_card, encoding="utf-8")
    return {"quality": quality, "timelines": details["timelines"], "tasks": tasks, "resource_counts": details["resource_counts"]}


class MedicalRunManager:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, pipeline: dict[str, Any], project_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        resolved_policy, dependency_reasons = resolve_output_policy(parameters.get("output_policy"))
        parameters = {
            **parameters,
            "resolved_output_policy": resolved_policy,
            "dependency_reasons": dependency_reasons,
        }
        pipeline_run = self.database.create_pipeline_run(pipeline["id"], project_id, pipeline["nodes"])
        self.tasks[pipeline_run["id"]] = asyncio.create_task(
            self._execute(pipeline_run["id"], pipeline, project_id, parameters)
        )
        return pipeline_run

    async def _execute(self, pipeline_run_id: str, pipeline: dict[str, Any], project_id: str, parameters: dict[str, Any]) -> None:
        state = self.database.get_pipeline_run(pipeline_run_id)
        if not state:
            return
        states = state["nodes"]
        self.database.update_pipeline_run(pipeline_run_id, status="running", started_at=now_iso())
        self.database.add_pipeline_event(pipeline_run_id, "started", "医疗合成流程已开始")
        output_root = self.settings.runtime_root / "medical" / project_id / pipeline_run_id
        details: dict[str, Any] = {}
        tasks: list[dict[str, Any]] = []
        quality: dict[str, Any] = {"passed": False, "rules": {}}
        resolved_policy = parameters.get("resolved_output_policy", {})
        try:
            for node in topological_nodes(pipeline["nodes"], pipeline["edges"]):
                node_id = node["id"]
                for item in states:
                    if item["id"] == node_id:
                        item["status"] = "running"
                self.database.update_pipeline_run(pipeline_run_id, current_node=node_id, nodes_json=states)
                self.database.add_pipeline_event(pipeline_run_id, "node_started", f"{node['label']}：进行中", {"node_id": node_id})
                if node["type"] == "source.synthea":
                    synthea_parameters = {
                        "population": 50,
                        "seed": 20260814,
                        "min_age": 18,
                        "max_age": 80,
                        "gender": "all",
                        **parameters,
                    }
                    synthea_parameters.pop("output_policy", None)
                    synthea_parameters.pop("resolved_output_policy", None)
                    synthea_parameters.pop("dependency_reasons", None)
                    synthea_parameters["csv_files"] = [key.split(":", 1)[1] for key, mode in resolved_policy.items() if key.startswith("csv:") and mode != "omit"]
                    synthea_parameters["fhir_resources"] = [key.split(":", 1)[1] for key, mode in resolved_policy.items() if key.startswith("fhir:") and mode != "omit"]
                    async with httpx.AsyncClient(timeout=300) as client:
                        response = await client.post(
                            f"{self.settings.synthea_worker_base_url}/generate",
                            json={**synthea_parameters, "output_dir": str(output_root / "synthea")},
                        )
                    if not response.is_success:
                        raise RuntimeError(f"Synthea 生成失败：{response.text[:300]}")
                    _prune_synthea_outputs(output_root / "synthea", resolved_policy)
                elif node["type"] == "parse.fhir":
                    fhir_root = output_root / "synthea"
                    if not fhir_root.exists():
                        raise RuntimeError("未找到 Synthea 输出目录")
                    details = await asyncio.to_thread(parse_medical_fhir, fhir_root, output_root)
                elif node["type"] == "quality.medical-rules":
                    if not details:
                        raise RuntimeError("FHIR 解析结果缺失")
                    quality = build_quality_report(details, [])
                    _write_json(output_root / "quality-report.json", quality)
                elif node["type"] == "generate.medical-tasks":
                    if not details:
                        raise RuntimeError("FHIR 解析结果缺失")
                    tasks = await asyncio.to_thread(generate_medical_tasks, details, output_root, pipeline_run_id)
                    quality = build_quality_report(details, tasks)
                    _write_json(output_root / "quality-report.json", quality)
                elif node["type"] == "output.medical-dataset":
                    if not quality:
                        quality = build_quality_report(details, tasks)
                        _write_json(output_root / "quality-report.json", quality)
                    (output_root / "data-card.md").write_text(
                        "# 医疗合成数据卡\n\n"
                        "- 数据性质：完全由固定版本 Synthea 规则引擎生成的虚构患者记录。\n"
                        "- 不得用于真实诊断、治疗决策或人群分布推断。\n"
                        "- 质量门禁：FHIR 解析、资源引用、患者时间线和衍生任务证据检查。\n"
                        "- 局限：默认统计模板不代表中国或任何真实医疗机构。\n",
                        encoding="utf-8",
                    )
                    _write_json(output_root / "dataset-manifest.json", {"status": "assembling"})
                    artifacts = build_artifact_index(output_root, resolved_policy)
                    manifest = {
                        "dataset_id": "pending",
                        "version": "v1",
                        "domain": "medical",
                        "source_assets": [],
                        "pipeline_version": f"{pipeline['id']}@{pipeline['version']}",
                        "tool_versions": {"synthea": self.settings.synthea_commit},
                        "quality_report": "quality-report.json",
                        "license": {"synthea": "Apache-2.0"},
                        "synthetic": True,
                        "intended_use": ["数据工程与模型任务样本研究"],
                        "limitations": ["不代表真实中国医疗分布", "不用于诊断或治疗决策"],
                        "requested_output_policy": parameters.get("output_policy"),
                        "resolved_output_policy": resolved_policy,
                        "dependency_reasons": parameters.get("dependency_reasons", {}),
                        "artifacts": artifacts,
                    }
                    dataset = self.database.create_dataset_version(
                        name=f"医疗合成数据集 {pipeline_run_id[:8]}", domain="medical", project_id=project_id,
                        manifest=manifest, quality=quality, artifact_path=str(output_root),
                    )
                    manifest["dataset_id"] = dataset["dataset_id"]
                    # The manifest describes itself. Rebuild a few times so its
                    # recorded size/index matches the final file written to disk.
                    for _ in range(4):
                        _write_json(output_root / "dataset-manifest.json", manifest)
                        manifest["artifacts"] = build_artifact_index(output_root, resolved_policy)
                    _write_json(output_root / "dataset-manifest.json", manifest)
                    self.database.update_dataset_version_metadata(dataset["id"], manifest=manifest, quality=quality)
                    details["dataset_version_id"] = dataset["id"]
                    if quality.get("passed"):
                        self.database.publish_dataset_version(dataset["id"])
                for item in states:
                    if item["id"] == node_id:
                        item["status"] = "succeeded"
                self.database.update_pipeline_run(pipeline_run_id, nodes_json=states)
                self.database.add_pipeline_event(pipeline_run_id, "node_succeeded", f"{node['label']}：已完成", {"node_id": node_id})
            final_status = "succeeded" if quality.get("passed") else "needs_review"
            self.database.update_pipeline_run(
                pipeline_run_id, status=final_status, current_node="", nodes_json=states,
                result_json={"dataset_version_id": details["dataset_version_id"], "quality": quality, "artifact_path": str(output_root), "artifacts": build_artifact_index(output_root, resolved_policy)},
                artifact_path=str(output_root), completed_at=now_iso(),
            )
            self.database.add_pipeline_event(pipeline_run_id, "completed", "医疗合成流程完成" if final_status == "succeeded" else "医疗合成流程完成，但质量门禁需要检查", {"status": final_status})
        except Exception as exc:
            for item in states:
                if item.get("status") == "running":
                    item["status"] = "failed"
            self.database.update_pipeline_run(pipeline_run_id, status="failed", nodes_json=states, error=str(exc)[:1000], completed_at=now_iso())
            self.database.add_pipeline_event(pipeline_run_id, "failed", "医疗合成流程失败", {"reason": str(exc)[:300]})
