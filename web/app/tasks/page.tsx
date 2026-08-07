"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useMemo, useState } from "react";
import { api, type Job, type Sample } from "../../lib/api";

const toolLabels: Record<string, string> = {
  "synthetic-data-kit": "Synthetic Data Kit",
  "easy-dataset": "Easy Dataset",
  synlogic: "SynLogic",
  kaqg: "KAQG",
  cleanlab: "Cleanlab",
  legacy: "历史实验",
};

const workflowLabels: Record<string, string> = {
  qa: "普通 QA",
  cot: "CoT 思维链",
  summary: "文档摘要",
  "cot-enhance": "CoT 增强",
  "document-qa": "文档问答",
  distillation: "数据蒸馏",
  "image-qa": "图片问答",
  evaluation: "评估数据",
  "arrow-maze": "Arrow Maze",
  "knowledge-graph-scq": "知识图谱单选题",
  "text-classification-audit": "文本分类质量检查",
};

export default function TasksPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [events, setEvents] = useState<
    { id: number; message: string; event_type: string; created_at: string }[]
  >([]);
  const [tool, setTool] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [needsAttention, setNeedsAttention] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    try {
      const query = needsAttention
        ? "/api/v2/jobs?status=failed&limit=200"
        : "/api/v2/jobs?status=succeeded&artifacts_only=true&limit=200";
      const rows = await api<Job[]>(query);
      const visibleRows = needsAttention
        ? rows
        : rows.filter((job) => job.workflow_type !== "arrow-maze-verify");
      setJobs(visibleRows);
      setSelected((current) =>
        visibleRows.find((item) => item.id === current?.id) ||
        visibleRows[0] ||
        null,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务读取失败");
    }
  }

  useEffect(() => {
    void load();
    // Reload when switching between completed and needs-attention views.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsAttention]);

  useEffect(() => {
    setSamples([]);
    setEvents([]);
    if (!selected) return;
    Promise.all([
      api<Sample[]>(`/api/v2/jobs/${selected.id}/samples`),
      api<{ id: number; message: string; event_type: string; created_at: string }[]>(
        `/api/v2/jobs/${selected.id}/event-log`,
      ),
    ])
      .then(([nextSamples, nextEvents]) => {
        setSamples(nextSamples);
        setEvents(nextEvents);
      })
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "任务详情读取失败"),
      );
  }, [selected]);

  const filtered = useMemo(
    () =>
      jobs.filter(
        (job) =>
          (!tool || job.tool_id === tool) &&
          (!workflow || job.workflow_type === workflow),
      ),
    [jobs, tool, workflow],
  );

  useEffect(() => {
    if (selected && !filtered.some((item) => item.id === selected.id)) {
      setSelected(filtered[0] || null);
    }
  }, [filtered, selected]);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>已完成任务</h1>
          <p>默认显示有真实产物的成功任务；失败记录放在“需要处理”中。</p>
        </div>
      </header>
      <section className="filter-bar">
        <label>
          工具
          <select value={tool} onChange={(e) => setTool(e.target.value)}>
            <option value="">全部工具</option>
            <option value="synthetic-data-kit">Synthetic Data Kit</option>
            <option value="easy-dataset">Easy Dataset</option>
            <option value="synlogic">SynLogic</option>
            <option value="kaqg">KAQG</option>
            <option value="cleanlab">Cleanlab</option>
            <option value="legacy">历史实验</option>
          </select>
        </label>
        <label>
          功能
          <select value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
            <option value="">全部功能</option>
            {Object.entries(workflowLabels).map(([id, label]) => (
              <option value={id} key={id}>{label}</option>
            ))}
          </select>
        </label>
        <label className="check-line attention-toggle">
          <input
            type="checkbox"
            checked={needsAttention}
            onChange={(e) => setNeedsAttention(e.target.checked)}
          />
          查看需要处理的任务
        </label>
      </section>
      {error && <div className="notice error">{error}</div>}
      <div className="task-layout">
        <section className="task-list" aria-label="任务列表">
          {filtered.length ? (
            filtered.map((job) => (
              <button
                className={selected?.id === job.id ? "task-item selected" : "task-item"}
                key={job.id}
                onClick={() => setSelected(job)}
              >
                <span>{toolLabels[job.tool_id || ""] || job.tool_name}</span>
                <strong>{workflowLabels[job.workflow_type] || job.workflow_type || job.tool_name}</strong>
                <small>{job.project_name || "历史项目"} · {new Date(job.created_at).toLocaleString("zh-CN")}</small>
                <em className={job.status}>{job.status === "failed" ? "需要处理" : "已完成"}</em>
              </button>
            ))
          ) : (
            <div className="empty-compact">当前筛选条件下没有任务。</div>
          )}
        </section>
        <section className="task-detail">
          {selected ? (
            <>
              <div className="section-heading">
                <div>
                  <h2>{workflowLabels[selected.workflow_type] || selected.tool_name}</h2>
                  <p>{toolLabels[selected.tool_id || ""]} · {selected.project_name || "历史项目"}</p>
                </div>
                <span className={`status-badge ${selected.status}`}>
                  {selected.status === "succeeded" ? "已完成" : "需要处理"}
                </span>
              </div>
              <div className="stage-list">
                {(selected.stages || []).map((stage) => (
                  <div className="stage-row" key={stage.id}>
                    <span className={`stage-mark ${stage.status}`} />
                    <strong>{stage.label}</strong>
                    <span>{stage.status === "succeeded" ? "已完成" : stage.status === "failed" ? "失败" : "等待"}</span>
                  </div>
                ))}
              </div>
              {selected.error && <div className="notice error">{selected.error}</div>}
              <details className="detail-block">
                <summary>输入参数</summary>
                <pre>{JSON.stringify(selected.input, null, 2)}</pre>
              </details>
              <details className="detail-block">
                <summary>运行日志（{events.length} 条）</summary>
                <div className="log-list">
                  {events.map((event) => (
                    <div key={event.id}>
                      <time>{new Date(event.created_at).toLocaleTimeString("zh-CN")}</time>
                      <span>{event.message}</span>
                    </div>
                  ))}
                </div>
              </details>
              <div className="detail-summary">
                <span>样本数量</span>
                <strong>{samples.length}</strong>
                <span>产物</span>
                <strong>{selected.artifact_path ? "已保存" : "无"}</strong>
              </div>
              {samples[0] && (
                <article className="sample-card compact">
                  <label>首条样本</label>
                  <p>{samples[0].question}</p>
                  <p className="pre-wrap">{samples[0].answer}</p>
                </article>
              )}
            </>
          ) : (
            <div className="empty-state small">
              <h2>选择一条任务查看详情</h2>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
