"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import {
  api,
  artifactUrl,
  type Job,
  type Sample,
} from "../../lib/api";
import { AssetPicker } from "../components/AssetPicker";
import { JobResult } from "../components/JobResult";
import { ProjectBar, useToolProject } from "../components/ProjectBar";

const difficultyLabels: Record<string, string> = {
  easy: "低难度",
  medium: "中难度",
  hard: "高难度",
};

export default function KaqgPage() {
  const workspace = useToolProject("kaqg");
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const [subjectName, setSubjectName] = useState("数据平台运维规范");
  const [easy, setEasy] = useState(3);
  const [medium, setMedium] = useState(3);
  const [hard, setHard] = useState(3);
  const [jobId, setJobId] = useState("");
  const [jobSummary, setJobSummary] = useState<Record<string, unknown> | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ services: { kaqg_worker?: { status: string } } }>(
      "/api/v2/integrations/status",
    )
      .then((value) => setReady(value.services.kaqg_worker?.status === "reachable"))
      .catch(() => setReady(false));
  }, []);

  useEffect(() => {
    setJobSummary(null);
    if (!workspace.project) {
      setJobId("");
      return;
    }
    api<Job[]>(`/api/v2/jobs?project_id=${workspace.project.id}&limit=20`)
      .then((jobs) => {
        const latest = jobs.find((job) => job.workflow_type === "knowledge-graph-scq");
        setJobId(latest?.id || "");
      })
      .catch(() => setJobId(""));
  }, [workspace.project]);

  const total = easy + medium + hard;
  const invalidCounts = [easy, medium, hard].some(
    (value) => !Number.isInteger(value) || value < 0 || value > 10,
  );

  async function run() {
    if (!workspace.project || !assetIds[0]) return;
    setBusy(true);
    setError("");
    setJobSummary(null);
    try {
      const job = await api<Job>(
        `/api/v2/kaqg/projects/${workspace.project.id}/jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            asset_id: assetIds[0],
            subject_name: subjectName.trim(),
            difficulty_counts: { easy, medium, hard },
            confirmed: true,
          }),
        },
      );
      setJobId(job.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "KAQG 任务启动失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <ProjectBar
        toolId="kaqg"
        title="KAQG"
        subtitle="从 PDF 抽取知识图谱，生成并评估低、中、高难度单选题。"
        connection={{
          label: ready ? "KAQG 与图谱服务可用" : "KAQG 或依赖服务未启动",
          tone: ready ? "ok" : "bad",
        }}
        workspace={workspace}
      />
      {workspace.project ? (
        <>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>生成知识图谱增强试题</h2>
                <p>只选择一个有文本层的 PDF；题目总数不能超过 30。</p>
              </div>
              <span className="count-summary">预计生成 {total} 题</span>
            </div>
            <AssetPicker
              projectId={workspace.project.id}
              selected={assetIds}
              onChange={setAssetIds}
              accept=".pdf"
              multiple={false}
              hint="支持 PDF，单文件不超过平台上传限制"
            />
            <div className="form-grid four">
              <label>
                知识领域
                <input
                  value={subjectName}
                  maxLength={120}
                  onChange={(event) => setSubjectName(event.target.value)}
                  placeholder="例如：数据平台运维规范"
                />
              </label>
              <DifficultyInput label="低难度" value={easy} onChange={setEasy} />
              <DifficultyInput label="中难度" value={medium} onChange={setMedium} />
              <DifficultyInput label="高难度" value={hard} onChange={setHard} />
            </div>
            <div className="run-row">
              <button
                className="button primary large"
                disabled={
                  busy ||
                  !ready ||
                  !assetIds[0] ||
                  !subjectName.trim() ||
                  invalidCounts ||
                  total < 1 ||
                  total > 30
                }
                onClick={() => void run()}
              >
                {busy ? "正在创建任务…" : "构建图谱并生成试题"}
              </button>
              <span className={invalidCounts || total < 1 || total > 30 ? "form-error" : "field-hint"}>
                {invalidCounts || total < 1 || total > 30
                  ? "每档需要 0～10 题，且总数为 1～30 题"
                  : "执行会调用文本模型，并使用本机 Neo4j 与 MQTT 服务"}
              </span>
            </div>
            {error && <div className="notice error">{error}</div>}
          </section>
          {jobSummary && <KaqgSummary value={jobSummary} />}
          <JobResult
            jobId={jobId}
            exportFormats={["jsonl"]}
            onLoaded={(job) => setJobSummary((job.result?.data || job.result || null) as Record<string, unknown> | null)}
            renderSample={(sample) => <KaqgQuestion sample={sample} />}
            resultActions={(job) => (
              <>
                <a className="button small" href={artifactUrl(job.id, "kaqg-graph")} download>
                  图谱 JSON
                </a>
                <a className="button small" href={artifactUrl(job.id, "kaqg-questions")} download>
                  题目 JSONL
                </a>
                <a className="button small" href={artifactUrl(job.id, "kaqg-record")} download>
                  实验记录
                </a>
              </>
            )}
          />
        </>
      ) : (
        <section className="empty-state">
          <h2>先建立一个 KAQG 项目</h2>
          <p>PDF、知识图谱、试题、评估结果和导出文件会保存在这个独立项目中。</p>
        </section>
      )}
    </div>
  );
}

function DifficultyInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {label}题数
      <input
        type="number"
        min={0}
        max={10}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function KaqgSummary({ value }: { value: Record<string, unknown> }) {
  const sections = Array.isArray(value.sections) ? value.sections : [];
  const facts = Array.isArray(value.facts) ? value.facts : [];
  return (
    <section className="panel compact-panel">
      <div className="section-heading">
        <div>
          <h2>知识图谱摘要</h2>
          <p>本次任务保存的图谱规模与部分抽取事实。</p>
        </div>
      </div>
      <div className="result-metrics">
        <Metric label="节点" value={value.node_count} />
        <Metric label="关系" value={value.relationship_count} />
        <Metric label="章节" value={value.section_count ?? sections.length} />
        <Metric label="试题" value={value.question_count} />
      </div>
      {facts.length > 0 && (
        <details className="fact-details">
          <summary>查看抽取事实（{facts.length}）</summary>
          <div className="fact-list">
            {facts.slice(0, 12).map((item, index) => {
              const fact = item as Record<string, unknown>;
              return (
                <div key={`${String(fact.subject)}-${index}`}>
                  <strong>{String(fact.subject || "未命名实体")}</strong>
                  <span>{String(fact.relation || "相关")}</span>
                  <span>{String(fact.object || "")}</span>
                </div>
              );
            })}
          </div>
        </details>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value == null ? "—" : String(value)}</strong>
    </div>
  );
}

function KaqgQuestion({ sample }: { sample: Sample }) {
  const options = (sample.generation.options || {}) as Record<string, unknown>;
  const pages = Array.isArray(sample.source.pages) ? sample.source.pages.join("、") : "—";
  const sections = Array.isArray(sample.source.sections)
    ? sample.source.sections.join("、")
    : "—";
  const difficulty = String(sample.quality.target_difficulty || "");
  return (
    <article className="sample-card kaqg-question">
      <div className="question-heading">
        <span className={`difficulty-tag ${difficulty}`}>
          {difficultyLabels[difficulty] || "未标注难度"}
        </span>
        <span>评估分数：{sample.quality.model_score ?? "—"}</span>
      </div>
      <h3>{sample.question}</h3>
      <ol className="option-list">
        {["A", "B", "C", "D"].map((letter) => (
          <li key={letter}>
            <strong>{letter}</strong>
            <span>{String(options[letter] || "未提供")}</span>
          </li>
        ))}
      </ol>
      <div className="answer-line"><strong>正确答案</strong><span>{sample.answer}</span></div>
      <div className="evidence-box">
        <strong>来源证据</strong>
        <p>{String(sample.source.evidence || "没有可显示的证据")}</p>
        <small>页码：{pages} · 章节：{sections}</small>
      </div>
    </article>
  );
}
