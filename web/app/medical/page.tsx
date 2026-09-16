"use client";

import Link from "next/link";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import { createMedicalGeneration, getPipelineRun, listMedicalOutputCatalog, listMedicalProjects, type MedicalOutput, type PipelineRun } from "../../lib/api";

const stageInfo = [
  { id: "synthea", title: "生成虚拟患者", type: "source.synthea", purpose: "根据人口参数和随机种子生成完全虚构的患者、就诊和临床事件。", input: "患者数量、年龄段、性别、随机种子", output: "FHIR R4 Bulk NDJSON 和 CSV", checks: "输出目录存在且至少产生一份文件", config: "患者参数和输出数据范围" },
  { id: "fhir", title: "解析 FHIR R4", type: "parse.fhir", purpose: "逐行读取 FHIR NDJSON，建立资源索引并构建患者时间线。", input: "Synthea FHIR R4 NDJSON", output: "资源计数、患者时间线、资源 ID 索引", checks: "JSON 可解析、资源类型和 ID 可识别", config: "无额外配置，始终流式处理" },
  { id: "rules", title: "结构与关系校验", type: "quality.medical-rules", purpose: "检查资源引用、患者关联、就诊关联、时间线以及输出依赖是否完整。", input: "FHIR 解析结果和最终输出策略", output: "质量报告和问题清单", checks: "无解析失败、无未解析患者引用", config: "固定医疗质量规则" },
  { id: "tasks", title: "生成医疗任务样本", type: "generate.medical-tasks", purpose: "基于患者时间线生成确定性任务，并为答案保留来源资源 ID。", input: "通过解析和校验的患者时间线", output: "medical-task-samples.jsonl", checks: "任务答案有资源证据，默认至少 20 条", config: "固定任务类型，不调用文本模型" },
  { id: "output", title: "封装医疗数据集", type: "output.medical-dataset", purpose: "把原始数据、派生数据、报告和版本信息封装成可追踪的数据资产。", input: "所有选定产物和最终质量报告", output: "Manifest、数据卡、产物索引、数据集版本", checks: "路径安全、质量通过才自动发布", config: "发布模式由第三步决定" },
];
const modeLabels = { omit: "不生成", internal: "内部使用", publish: "生成并发布" };
const categoryLabels = { csv: "CSV 表格", fhir: "FHIR 资源", derived: "派生结果", report: "报告与清单" };
const MEDICAL_RUN_STORAGE_KEY = "medical-generation:last-run";
type SavedMedicalRun = { runId?: string; projectId?: string; name: string; submittedAt: string };

function flowNodes(selected: string): Node[] { return stageInfo.map((item, index) => ({ id: item.id, position: { x: 40 + index * 205, y: 100 }, data: { label: item.title }, style: { width: 170, border: selected === item.id ? "2px solid #2563eb" : "1px solid #c8cfd8", borderRadius: 8, background: index === 0 ? "#e8f1ff" : index === 1 ? "#eef7f1" : index === 2 ? "#fff0ee" : index === 3 ? "#f2edff" : "#edf4f5", padding: 12, fontSize: 13 } })); }
function flowEdges(): Edge[] { return stageInfo.slice(0, -1).map((item, index) => ({ id: `${item.id}-${stageInfo[index + 1].id}`, source: item.id, target: stageInfo[index + 1].id })); }
function statusLabel(status: string) { return ({ queued: "等待执行", running: "执行中", succeeded: "质量通过并已发布", needs_review: "完成但需要检查", failed: "执行失败", pending: "等待", cancelled: "已取消" } as Record<string, string>)[status] || status; }

export default function MedicalPage() {
  const [catalog, setCatalog] = useState<MedicalOutput[]>([]);
  const [policy, setPolicy] = useState<Record<string, "omit" | "internal" | "publish">>({});
  const [name, setName] = useState("医疗合成数据集");
  const [population, setPopulation] = useState(50); const [seed, setSeed] = useState(20260814); const [minAge, setMinAge] = useState(18); const [maxAge, setMaxAge] = useState(80); const [gender, setGender] = useState("all");
  const [selectedStage, setSelectedStage] = useState("synthea"); const [run, setRun] = useState<PipelineRun | null>(null); const [projectId, setProjectId] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);

  useEffect(() => { void listMedicalOutputCatalog().then((items) => { setCatalog(items); setPolicy(Object.fromEntries(items.map((item) => [item.id, "publish"]))); }).catch((error: Error) => setMessage(error.message)); }, []);
  useEffect(() => {
    let disposed = false;
    async function recoverRun() {
      const raw = window.localStorage.getItem(MEDICAL_RUN_STORAGE_KEY);
      let saved: SavedMedicalRun | null = null;
      if (raw) {
        try { saved = JSON.parse(raw) as SavedMedicalRun; } catch { window.localStorage.removeItem(MEDICAL_RUN_STORAGE_KEY); return; }
      }
      let runId = saved?.runId;
      let recoveredProjectId = saved?.projectId;
      if (saved?.name) setName(saved.name);
      const projects = await listMedicalProjects();
      if (!runId && saved?.name) {
        const submittedAt = Date.parse(saved.submittedAt);
        const candidate = projects.find((project) => project.display_name === saved?.name && Date.parse(project.created_at) >= submittedAt - 5000 && project.latest_pipeline_run_id);
        runId = candidate?.latest_pipeline_run_id || undefined;
        recoveredProjectId = candidate?.id || recoveredProjectId;
        if (runId && recoveredProjectId) window.localStorage.setItem(MEDICAL_RUN_STORAGE_KEY, JSON.stringify({ ...saved, runId, projectId: recoveredProjectId }));
      }
      // 如果浏览器没有保留本地状态，仍可从最近的后台运行中恢复刷新前的任务。
      if (!runId) {
        const candidate = projects.filter((project) => project.latest_pipeline_run_id && ["queued", "running"].includes(project.latest_pipeline_run_status || "")).sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0];
        runId = candidate?.latest_pipeline_run_id || undefined;
        recoveredProjectId = candidate?.id || recoveredProjectId;
        if (candidate) setName(candidate.display_name);
      }
      if (!runId) return;
      const recovered = await getPipelineRun(runId);
      if (disposed) return;
      setRun(recovered);
      setProjectId(recoveredProjectId || recovered.project_id || "");
      setMessage("已从服务端恢复本次运行；刷新页面不会中断后台流程。");
    }
    void recoverRun().catch((error: Error) => setMessage(`暂时无法恢复运行状态，请稍后刷新重试：${error.message}`));
    return () => { disposed = true; };
  }, []);
  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const runId = run.id;
    const poll = () => void getPipelineRun(runId).then(setRun).catch((error: Error) => setMessage(error.message));
    const timer = window.setInterval(poll, 1200);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);
  const resolved = useMemo(() => { const ranks = ["omit", "internal", "publish"]; const next = Object.fromEntries(catalog.map((item) => [item.id, policy[item.id] || "omit"])) as Record<string, "omit" | "internal" | "publish">; let changed = true; while (changed) { changed = false; catalog.forEach((item) => { const minimum = item.required_min; if (ranks.indexOf(next[item.id]) < ranks.indexOf(minimum)) { next[item.id] = minimum; changed = true; } item.depends_on.forEach((dependency) => { if (ranks.indexOf(next[dependency]) < ranks.indexOf(next[item.id])) { next[dependency] = next[item.id]; changed = true; } }); }); } return next; }, [catalog, policy]);
  const nodes = useMemo(() => flowNodes(selectedStage), [selectedStage]); const edges = useMemo(flowEdges, []); const selected = stageInfo.find((item) => item.id === selectedStage) || stageInfo[0]; const grouped = useMemo(() => catalog.reduce<Record<string, MedicalOutput[]>>((groups, item) => { (groups[item.category] ||= []).push(item); return groups; }, {}), [catalog]);
  function setPreset(kind: "recommended" | "minimal" | "all") { if (kind === "minimal") setPolicy(Object.fromEntries(catalog.map((item) => [item.id, item.required_min]))); else setPolicy(Object.fromEntries(catalog.map((item) => [item.id, "publish"] as const))); }
  async function generate() {
    if (!name.trim() || minAge > maxAge) return;
    setBusy(true); setMessage("");
    const submittedAt = new Date().toISOString();
    window.localStorage.setItem(MEDICAL_RUN_STORAGE_KEY, JSON.stringify({ name: name.trim(), submittedAt } satisfies SavedMedicalRun));
    try {
      const result = await createMedicalGeneration({ name: name.trim(), population, seed, min_age: minAge, max_age: maxAge, gender, output_policy: policy });
      window.localStorage.setItem(MEDICAL_RUN_STORAGE_KEY, JSON.stringify({ runId: result.id, projectId: result.project.id, name: name.trim(), submittedAt } satisfies SavedMedicalRun));
      setRun(result); setProjectId(result.project.id); setMessage(`已创建项目“${result.project.display_name}”，系统自动生成项目 ID：${result.project.id}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "提交失败"); }
    finally { setBusy(false); }
  }
  return <div className="page wide-page"><header className="page-header"><div><h1>医疗数据生成</h1><p>一次填写、一次生成、一个项目。系统使用固定版本 Synthea 生成完全虚构的医疗数据，再完成 FHIR R4 解析、结构关系检查、任务样本和数据集封装。</p></div></header>
    <section className="panel medical-step"><div className="step-heading"><span>1</span><div><h2>项目与虚拟患者参数</h2><p>项目 ID 由系统自动生成，避免用户维护容易出错的技术编号。</p></div></div><div className="form-grid"><label>项目名称<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：项目一 医疗合成数据集" /></label><label>患者数量<input type="number" min="1" max="500" value={population} onChange={(event) => setPopulation(Number(event.target.value))}/></label><label>随机种子<input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))}/></label><label>最小年龄<input type="number" min="0" max="110" value={minAge} onChange={(event) => setMinAge(Number(event.target.value))}/></label><label>最大年龄<input type="number" min="0" max="110" value={maxAge} onChange={(event) => setMaxAge(Number(event.target.value))}/></label><label>性别<select value={gender} onChange={(event) => setGender(event.target.value)}><option value="all">全部</option><option value="M">男性</option><option value="F">女性</option></select></label></div><p className="muted">默认生成 50 名患者。数据为 Synthea 虚构记录，不代表真实中国医疗分布，不能用于诊断或治疗。</p></section>
    <section className="panel medical-step" id="workflow"><div className="step-heading"><span>2</span><div><h2>固定医疗流程</h2><p>流程顺序固定，点击节点查看该步骤的职责和检查内容；用户不能改动拓扑。</p></div></div><div className="workflow-layout medical-workflow"><section className="workflow-canvas"><ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} elementsSelectable onNodeClick={(_, node) => setSelectedStage(node.id)}><Background gap={18} size={1}/><Controls showInteractive={false}/></ReactFlow></section><aside className="workflow-inspector"><h2>{selected.title}</h2><p>{selected.purpose}</p><h3>输入与输出</h3><p><strong>输入：</strong>{selected.input}</p><p><strong>输出：</strong>{selected.output}</p><h3>检查</h3><p>{selected.checks}</p><h3>可配置项</h3><p>{selected.config}</p></aside></div></section>
    <section className="panel medical-step"><div className="step-heading"><span>3</span><div><h2>选择要生成的数据</h2><p>选择会影响 Synthea 实际生成的内容。依赖项会自动补齐，并在右侧说明原因。</p></div></div><div className="preset-row"><span>快捷方案：</span><button className="button" onClick={() => setPreset("recommended")}>推荐完整医疗数据</button><button className="button" onClick={() => setPreset("minimal")}>最小可验证闭环</button><button className="button" onClick={() => setPreset("all")}>全部输出</button></div><div className="output-groups">{Object.entries(grouped).map(([category, items]) => <section className="output-group" key={category}><h3>{categoryLabels[category as keyof typeof categoryLabels] || category}</h3>{items.map((item) => { const auto = resolved[item.id] !== policy[item.id]; return <div className={`output-row ${auto ? "auto-output" : ""}`} key={item.id}><div className="output-copy"><strong>{item.label}</strong><span>{item.description}</span>{item.depends_on.length > 0 && <small>依赖：{item.depends_on.map((id) => catalog.find((candidate) => candidate.id === id)?.label || id).join("、")}</small>}{auto && <small className="dependency-note">系统已自动调整：该项是其他已选数据的必要依赖</small>}</div><select value={resolved[item.id]} onChange={(event) => setPolicy((current) => ({ ...current, [item.id]: event.target.value as "omit" | "internal" | "publish" }))}><option value="omit">{modeLabels.omit}</option><option value="internal">{modeLabels.internal}</option><option value="publish">{modeLabels.publish}</option></select></div>; })}</section>)}</div><p className="muted">解析后将生成 {Object.values(resolved).filter((value) => value !== "omit").length} 项，其中 {Object.values(resolved).filter((value) => value === "internal").length} 项仅供流程内部使用。系统会优先保证患者、就诊和关联资源完整。</p></section>
    <section className="panel medical-step"><div className="step-heading"><span>4</span><div><h2>确认并开始生成</h2><p>提交后系统自动创建新项目并进入上方流程图的运行状态。</p></div></div><div className="review-summary"><span>项目：{name || "未填写"}</span><span>患者：{population} 人</span><span>发布数据：{Object.values(resolved).filter((value) => value === "publish").length} 项</span><span>内部依赖：{Object.values(resolved).filter((value) => value === "internal").length} 项</span></div>{message && <div className={message.includes("失败") || message.includes("请") ? "notice error" : "notice"}>{message}</div>}<button className="button primary" disabled={busy || !name.trim() || minAge > maxAge || !catalog.length} onClick={() => void generate()}>{busy ? "提交中…" : "创建项目并生成医疗数据"}</button>{projectId && <Link className="button" href={`/datasets?project=${projectId}`}>查看该项目数据资产</Link>}</section>
    {run && <section className="panel"><h2>本次运行：{statusLabel(run.status)}</h2><div className="stage-list">{run.nodes.map((item) => <button className="stage-row stage-row-button" key={item.id} onClick={() => setSelectedStage(item.id)}><span className={`stage-status ${item.status}`}/><strong>{item.label}</strong><span>{statusLabel(item.status)}</span></button>)}</div>{run.error && <div className="notice error">{run.error}</div>}{run.result && <pre className="result-pre">{JSON.stringify(run.result, null, 2)}</pre>}</section>}
  </div>;
}
