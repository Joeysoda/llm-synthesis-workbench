from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException
from neo4j import GraphDatabase
from openai import OpenAI
from pydantic import BaseModel, Field

KAQG_SRC = Path(os.environ.get("KAQG_REPO", "/opt/upstream/kaqg")) / "src"
if str(KAQG_SRC) not in sys.path:
    sys.path.insert(0, str(KAQG_SRC))

try:
    from evaluation.features import ScqFeatures
except Exception as exc:  # pragma: no cover - Docker health reports this precisely
    raise RuntimeError(f"无法加载 KAQG 评分特征：{exc}") from exc

app = FastAPI(title="KAQG compatibility worker", version="0.1.0")
RUN_STATUS: dict[str, dict[str, Any]] = {}

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
FEATURE_WEIGHTS = [1, 1, 1.5, 1, 1, 1, 1.2]
TARGET_GRADES = {"easy": 10.0, "medium": 14.0, "hard": 18.0}
DIFFICULTY_CODES = {"easy": 30, "medium": 50, "hard": 70}


class RunRequest(BaseModel):
    pdf_path: str
    run_dir: str
    project_id: str
    run_id: str
    subject_name: str = Field(min_length=1, max_length=120)
    difficulty_counts: dict[str, int]
    model: str
    llm_base_url: str
    llm_api_key: str
    kaqg_commit: str
    graph_only: bool = False


def _set_run_status(
    run_id: str,
    stage: str,
    message: str,
    *,
    completed: int = 0,
    total: int = 0,
    status: str = "running",
) -> None:
    RUN_STATUS[run_id] = {
        "status": status,
        "stage": stage,
        "message": message,
        "progress": {"completed": completed, "total": total} if total else {},
    }


def _json_from_text(text: str) -> Any:
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    first_object = min(
        (index for index in (value.find("{"), value.find("[")) if index >= 0),
        default=-1,
    )
    if first_object > 0:
        value = value[first_object:]
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        for closing in ("}", "]"):
            end = value.rfind(closing)
            if end >= 0:
                try:
                    return json.loads(value[: end + 1])
                except json.JSONDecodeError:
                    continue
        raise ValueError("模型没有返回可解析的 JSON")


def _chat(client: OpenAI, model: str, messages: list[dict[str, str]]) -> Any:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return _json_from_text(content)


def _read_pdf(path: Path) -> list[dict[str, Any]]:
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ValueError(f"PDF 无法打开：{exc}") from exc
    pages = []
    for index, page in enumerate(document):
        text = page.get_text("text").strip()
        pages.append({"page": index + 1, "text": text})
    document.close()
    if sum(len(page["text"]) for page in pages) < 200:
        raise ValueError("PDF 没有足够的可提取文本，请使用有文本层的 PDF")
    return pages


def _extract_page(client: OpenAI, model: str, page: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""从下面中文教材页面中抽取知识图谱。只返回 JSON，不要解释。
格式：{{"section":"章节名称","triplets":[{{"subject":"实体","relation":"关系","object":"实体或事实","evidence":"支持原句"}}]}}
要求：每个三元组忠于原文；实体名称简洁；最多 12 条；evidence 保留足以核验的原文片段。

第 {page['page']} 页：
{page['text']}
"""
    value = _chat(
        client,
        model,
        [
            {"role": "system", "content": "你是 KAQG 文档事实与概念抽取器。"},
            {"role": "user", "content": prompt},
        ],
    )
    if not isinstance(value, dict) or not isinstance(value.get("triplets"), list):
        raise ValueError(f"第 {page['page']} 页图谱抽取结果格式错误")
    rows = []
    for item in value["triplets"]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "")).strip()
        relation = str(item.get("relation", "")).strip()
        obj = str(item.get("object", "")).strip()
        if subject and relation and obj:
            rows.append(
                {
                    "subject": subject,
                    "relation": relation,
                    "object": obj,
                    "evidence": str(item.get("evidence", "")).strip(),
                    "page": page["page"],
                    "section": str(value.get("section", f"第 {page['page']} 页")),
                }
            )
    return {"section": value.get("section", f"第 {page['page']} 页"), "triplets": rows}


def _reset_graph(driver) -> None:
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()


def _write_graph(driver, triplets: list[dict[str, Any]], run_id: str) -> None:
    with driver.session() as session:
        for item in triplets:
            session.run(
                """
                MERGE (s:Concept {name: $subject, run_id: $run_id})
                MERGE (o:Concept {name: $object, run_id: $run_id})
                CREATE (s)-[:RELATED {
                    relation: $relation, evidence: $evidence, page: $page,
                    section: $section, run_id: $run_id
                }]->(o)
                """,
                subject=item["subject"],
                object=item["object"],
                relation=item["relation"],
                evidence=item["evidence"],
                page=item["page"],
                section=item["section"],
                run_id=run_id,
            ).consume()


def _export_graph(driver, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with driver.session() as session:
        nodes = [
            dict(record["node"])
            for record in session.run(
                "MATCH (n {run_id: $run_id}) RETURN properties(n) AS node ORDER BY n.name",
                run_id=run_id,
            )
        ]
        relationships = [
            {
                "source": record["source"],
                "target": record["target"],
                **dict(record["relationship"]),
            }
            for record in session.run(
                """
                MATCH (s {run_id: $run_id})-[r:RELATED {run_id: $run_id}]->(o)
                RETURN s.name AS source, o.name AS target, properties(r) AS relationship
                """,
                run_id=run_id,
            )
        ]
    return nodes, relationships


def _question_prompt(difficulty: str, facts: list[dict[str, Any]]) -> str:
    descriptors = {
        "easy": "基础记忆题，题干简洁，干扰项明显不同",
        "medium": "理解与综合题，需要关联两条事实，干扰项具有一定迷惑性",
        "hard": "分析判断题，需要比较规则、条件或因果关系，干扰项高度可信",
    }
    material = [
        {
            "fact_id": index,
            "subject": item["subject"],
            "relation": item["relation"],
            "object": item["object"],
            "evidence": item["evidence"],
        }
        for index, item in enumerate(facts)
    ]
    return f"""使用给定事实生成一道中文单选题，难度要求：{descriptors[difficulty]}。
只返回 JSON：
{{"stem":"题干","option_A":"选项","option_B":"选项","option_C":"选项","option_D":"选项","answer":"A|B|C|D","evidence_fact_ids":[0]}}
要求：唯一正确答案必须能由事实直接支持；不能加入材料外知识；四个选项不得重复。
事实：{json.dumps(material, ensure_ascii=False)}
"""


def _generate_question(
    client: OpenAI, model: str, difficulty: str, facts: list[dict[str, Any]]
) -> dict[str, Any]:
    value = _chat(
        client,
        model,
        [
            {"role": "system", "content": "你是 KAQG 难度可控单选题生成器。"},
            {"role": "user", "content": _question_prompt(difficulty, facts)},
        ],
    )
    required = {"stem", "option_A", "option_B", "option_C", "option_D", "answer"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("题目生成结果字段不完整")
    answer = str(value["answer"]).strip().upper()
    if answer not in {"A", "B", "C", "D"}:
        raise ValueError("题目答案不是 A、B、C、D 之一")
    options = [str(value[f"option_{letter}"]).strip() for letter in "ABCD"]
    if any(not option for option in options) or len(set(options)) != 4:
        raise ValueError("题目选项为空或重复")
    value["answer"] = answer
    value["evidence_fact_ids"] = [
        int(index)
        for index in value.get("evidence_fact_ids", [])
        if isinstance(index, int) and 0 <= index < len(facts)
    ]
    if not value["evidence_fact_ids"]:
        value["evidence_fact_ids"] = [0]
    return value


def _stem_length_score(stem: str) -> int:
    units = len(re.findall(r"[\u4e00-\u9fff]", stem)) + len(re.findall(r"[A-Za-z]+", stem))
    return 1 if units <= 15 else 2 if units <= 30 else 3


def _evaluate_question(client: OpenAI, model: str, question: dict[str, Any]) -> dict[str, int]:
    keys = ScqFeatures.keys[1:]
    prompt = f"""严格评估下面单选题，只返回 JSON。每项只能为 1、2、3。
字段：{json.dumps(keys, ensure_ascii=False)}
1 表示低，2 表示中，3 表示高。对技术术语密度、认知层次、选项长度、选项相似度、题干选项相关性反向难度、强干扰项数量分别评分。
题目：{json.dumps(question, ensure_ascii=False)}
"""
    value = _chat(
        client,
        model,
        [
            {"role": "system", "content": "你是 KAQG ScqEvaluator 兼容评分器。"},
            {"role": "user", "content": prompt},
        ],
    )
    if not isinstance(value, dict):
        raise ValueError("评估结果不是 JSON 对象")
    result = {"stem_length": _stem_length_score(str(question["stem"]))}
    for key in keys:
        score = int(value.get(key, 0))
        if score not in {1, 2, 3}:
            raise ValueError(f"评估字段 {key} 超出 1 到 3")
        result[key] = score
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    driver = GraphDatabase.driver(NEO4J_URI, auth=None)
    try:
        driver.verify_connectivity()
    finally:
        driver.close()
    with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=3):
        pass
    return {
        "status": "ok",
        "kaqg_repo": str(KAQG_SRC.parent),
        "feature_count": len(ScqFeatures.keys),
        "neo4j": NEO4J_URI,
        "mqtt": f"{MQTT_HOST}:{MQTT_PORT}",
    }


@app.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, Any]:
    status = RUN_STATUS.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务尚未进入 worker")
    return status


@app.post("/run")
def run(payload: RunRequest) -> dict[str, Any]:
    _set_run_status(payload.run_id, "check", "检查 KAQG worker、Neo4j 与 Mosquitto")
    source = Path(payload.pdf_path).resolve()
    run_dir = Path(payload.run_dir).resolve()
    runtime_root = Path("/app/runtime").resolve()
    if runtime_root not in source.parents or runtime_root not in run_dir.parents:
        raise HTTPException(status_code=422, detail="文件路径不在共享 runtime 中")
    run_dir.mkdir(parents=True, exist_ok=True)
    if not payload.llm_api_key:
        raise HTTPException(status_code=503, detail="DeepSeek 密钥未配置")
    client = OpenAI(api_key=payload.llm_api_key, base_url=payload.llm_base_url)
    driver = GraphDatabase.driver(NEO4J_URI, auth=None)
    try:
        with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=3):
            pass
        driver.verify_connectivity()
        _reset_graph(driver)
        _set_run_status(payload.run_id, "parse", "解析 PDF 并抽取页面事实")
        pages = _read_pdf(source)
        sections = []
        triplets = []
        for page in pages:
            extracted = _extract_page(client, payload.model, page)
            sections.append({"page": page["page"], "title": extracted["section"]})
            triplets.extend(extracted["triplets"])
        if not triplets:
            raise ValueError("没有从 PDF 中抽取到可用事实")
        _set_run_status(payload.run_id, "graph", "写入并导出知识图谱")
        _write_graph(driver, triplets, payload.run_id)
        nodes, relationships = _export_graph(driver, payload.run_id)
        graph_path = run_dir / "kaqg-graph.json"
        graph_path.write_text(
            json.dumps(
                {"nodes": nodes, "relationships": relationships, "sections": sections},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        samples = []
        if not payload.graph_only:
            total_questions = sum(
                int(value) for value in payload.difficulty_counts.values()
            )
            cursor = 0
            generated_questions = []
            _set_run_status(
                payload.run_id,
                "generate",
                "生成难度可控试题",
                completed=0,
                total=total_questions,
            )
            for difficulty in ("easy", "medium", "hard"):
                count = int(payload.difficulty_counts.get(difficulty, 0))
                for _ in range(count):
                    material = [
                        triplets[(cursor + offset) % len(triplets)]
                        for offset in range(min(8, len(triplets)))
                    ]
                    cursor += 3
                    question = _generate_question(client, payload.model, difficulty, material)
                    generated_questions.append((difficulty, material, question))
                    _set_run_status(
                        payload.run_id,
                        "generate",
                        "生成难度可控试题",
                        completed=len(generated_questions),
                        total=total_questions,
                    )

            _set_run_status(
                payload.run_id,
                "evaluate",
                "逐题评估结构与难度",
                completed=0,
                total=total_questions,
            )
            for difficulty, material, question in generated_questions:
                feature_scores = _evaluate_question(client, payload.model, question)
                weighted_score = sum(
                    feature_scores[key] * weight
                    for key, weight in zip(ScqFeatures.keys, FEATURE_WEIGHTS)
                )
                evidence = [material[index] for index in question["evidence_fact_ids"]]
                answer_letter = question["answer"]
                samples.append(
                    {
                        "task_type": "knowledge_graph_scq",
                        "question": question["stem"],
                        "answer": f"{answer_letter}. {question[f'option_{answer_letter}']}",
                        "reasoning": "",
                        "source": {
                            "file": source.name,
                            "pages": sorted({item["page"] for item in evidence}),
                            "sections": sorted({item["section"] for item in evidence}),
                            "evidence": "\n".join(item["evidence"] for item in evidence),
                            "facts": evidence,
                        },
                        "generation": {
                            "tool": "kaqg",
                            "commit": payload.kaqg_commit,
                            "model": payload.model,
                            "options": {
                                letter: question[f"option_{letter}"]
                                for letter in "ABCD"
                            },
                        },
                        "quality": {
                            "rule_passed": (
                                abs(TARGET_GRADES[difficulty] - weighted_score) <= 1.5
                            ),
                            "human_status": "pending",
                            "target_difficulty": difficulty,
                            "difficulty_code": DIFFICULTY_CODES[difficulty],
                            "model_score": round(weighted_score, 2),
                            "feature_scores": feature_scores,
                        },
                    }
                )
                _set_run_status(
                    payload.run_id,
                    "evaluate",
                    "逐题评估结构与难度",
                    completed=len(samples),
                    total=total_questions,
                )
        _set_run_status(payload.run_id, "export", "保存图谱、题目和实验记录")
        questions_path = run_dir / "kaqg-questions.jsonl"
        questions_path.write_text(
            "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples) + ("\n" if samples else ""),
            encoding="utf-8",
        )
        record_path = run_dir / "kaqg-run-record.json"
        record = {
            "tool": "kaqg",
            "commit": payload.kaqg_commit,
            "model": payload.model,
            "subject_name": payload.subject_name,
            "node_count": len(nodes),
            "relationship_count": len(relationships),
            "section_count": len(sections),
            "question_count": len(samples),
            "artifacts": [str(graph_path), str(questions_path)],
        }
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {
            **record,
            "sections": sections,
            "facts": triplets[:30],
            "graph_path": str(graph_path),
            "questions_path": str(questions_path),
            "artifact_path": str(record_path),
            "samples": samples,
        }
        _set_run_status(
            payload.run_id,
            "export",
            "KAQG 任务完成",
            completed=len(samples),
            total=len(samples),
            status="succeeded",
        )
        return result
    except ValueError as exc:
        _set_run_status(payload.run_id, "failed", str(exc), status="failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _set_run_status(
            payload.run_id,
            "failed",
            f"KAQG worker 执行异常：{type(exc).__name__}",
            status="failed",
        )
        raise HTTPException(status_code=500, detail=f"KAQG worker 执行异常：{type(exc).__name__}: {exc}") from exc
    finally:
        try:
            _reset_graph(driver)
        except Exception:
            pass
        driver.close()
