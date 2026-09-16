from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from typing import Any


OPERATORS: list[dict[str, Any]] = [
    {"type": "source.asset", "label": "上传文件", "category": "数据源", "inputs": [], "outputs": ["document", "classification"], "risk": "low", "resources": "local"},
    {"type": "source.history", "label": "历史任务结果", "category": "数据源", "inputs": [], "outputs": ["dataset"], "risk": "low", "resources": "local"},
    {"type": "source.synthea", "label": "Synthea 合成患者", "category": "数据源", "inputs": [], "outputs": ["fhir"], "risk": "medium", "resources": "java"},
    {"type": "parse.document", "label": "文档解析", "category": "解析", "inputs": ["document"], "outputs": ["records"], "risk": "low", "resources": "local"},
    {"type": "parse.fhir", "label": "FHIR R4 解析", "category": "解析", "inputs": ["fhir"], "outputs": ["timeline"], "risk": "low", "resources": "local"},
    {"type": "govern.normalize", "label": "格式标准化", "category": "治理", "inputs": ["records", "dataset"], "outputs": ["records"], "risk": "low", "resources": "local"},
    {"type": "quality.cleanlab", "label": "Cleanlab 质量检查", "category": "质量", "inputs": ["classification"], "outputs": ["classification"], "risk": "medium", "resources": "python"},
    {"type": "quality.medical-rules", "label": "医疗结构与关系校验", "category": "质量", "inputs": ["timeline"], "outputs": ["timeline"], "risk": "low", "resources": "local"},
    {"type": "generate.synthetic", "label": "Synthetic Data Kit", "category": "生成", "inputs": ["records"], "outputs": ["qa"], "risk": "medium", "resources": "llm"},
    {"type": "generate.easy-dataset", "label": "Easy Dataset", "category": "生成", "inputs": ["records"], "outputs": ["qa"], "risk": "medium", "resources": "llm"},
    {"type": "generate.medical-tasks", "label": "医疗任务样本", "category": "生成", "inputs": ["timeline"], "outputs": ["medical_tasks"], "risk": "low", "resources": "local"},
    {"type": "quality.llm-judge", "label": "LLM Judge", "category": "质量", "inputs": ["qa", "medical_tasks"], "outputs": ["qa", "medical_tasks"], "risk": "medium", "resources": "llm"},
    {"type": "review.human", "label": "人工审核", "category": "质量", "inputs": ["qa", "medical_tasks", "classification"], "outputs": ["qa", "medical_tasks", "classification"], "risk": "low", "resources": "user"},
    {"type": "output.dataset", "label": "数据集封装", "category": "输出", "inputs": ["qa", "classification", "medical_tasks"], "outputs": [], "risk": "low", "resources": "local"},
    {"type": "output.medical-dataset", "label": "医疗数据集封装", "category": "输出", "inputs": ["medical_tasks"], "outputs": [], "risk": "low", "resources": "local"},
]

OPERATOR_BY_TYPE = {item["type"]: item for item in OPERATORS}


def node(node_id: str, node_type: str, label: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "label": label, "config": config or {}}


PIPELINE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "medical-synthea",
        "name": "医疗合成数据闭环",
        "description": "Synthea 生成虚构患者后，完成 FHIR 解析、医疗规则校验和带证据任务样本生成。",
        "domain": "medical",
        "nodes": [
            node("synthea", "source.synthea", "生成虚构患者"),
            node("fhir", "parse.fhir", "解析 FHIR R4"),
            node("rules", "quality.medical-rules", "结构与关系校验"),
            node("tasks", "generate.medical-tasks", "生成医疗任务样本"),
            node("output", "output.medical-dataset", "封装医疗数据集"),
        ],
        "edges": [{"source": a, "target": b} for a, b in [("synthea", "fhir"), ("fhir", "rules"), ("rules", "tasks"), ("tasks", "output")]],
    },
]

# The standalone workflow editor is retired; this is the only public template.


def templates() -> list[dict[str, Any]]:
    return deepcopy(PIPELINE_TEMPLATES)


def validate_pipeline(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    node_ids = [str(item.get("id", "")) for item in nodes]
    if len(node_ids) != len(set(node_ids)):
        errors.append({"code": "duplicate_node", "message": "节点 ID 不能重复"})
    known = set(node_ids)
    for item in nodes:
        if item.get("type") not in OPERATOR_BY_TYPE:
            errors.append({"code": "unknown_operator", "message": f"不支持的节点类型：{item.get('type')}"})
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in known or target not in known:
            errors.append({"code": "missing_node", "message": "连线引用了不存在的节点"})
        elif source == target:
            errors.append({"code": "self_cycle", "message": "节点不能连接到自身"})

    types = {item["id"]: item.get("type") for item in nodes if item.get("id")}
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source in known and target in known and source != target:
            incoming[target].append(source)
            outgoing[source].append(target)
            source_operator = OPERATOR_BY_TYPE.get(types.get(source, ""), {})
            target_operator = OPERATOR_BY_TYPE.get(types.get(target, ""), {})
            if set(source_operator.get("outputs", [])) and not set(source_operator.get("outputs", [])).intersection(target_operator.get("inputs", [])):
                errors.append({"code": "type_mismatch", "message": f"“{source}”的输出不能连接到“{target}”"})

    terminals = [item for item in nodes if str(item.get("type", "")).startswith("output.")]
    if len(terminals) != 1:
        errors.append({"code": "output_count", "message": "流程必须且只能有一个输出节点"})
    elif outgoing.get(terminals[0]["id"]):
        errors.append({"code": "output_connected", "message": "输出节点不能再连接到后续节点"})
    if nodes and not [item for item in nodes if str(item.get("type", "")).startswith("source.")]:
        errors.append({"code": "missing_source", "message": "流程必须从一个数据源节点开始"})

    indegree = {node_id: len(incoming[node_id]) for node_id in known}
    queue = deque(node_id for node_id, count in indegree.items() if count == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if nodes and visited != len(nodes):
        errors.append({"code": "cycle", "message": "流程中存在循环依赖"})
    return {"valid": not errors, "errors": errors}


def topological_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in nodes}
    incoming = {item["id"]: 0 for item in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]] += 1
    queue = deque(item["id"] for item in nodes if incoming[item["id"]] == 0)
    ordered: list[dict[str, Any]] = []
    while queue:
        current = queue.popleft()
        ordered.append(by_id[current])
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return ordered
