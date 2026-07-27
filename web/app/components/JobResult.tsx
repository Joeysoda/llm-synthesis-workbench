"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import {
  API_BASE,
  getJob,
  getSamples,
  patchSample,
  type Job,
  type Sample,
} from "../../lib/api";

const statusLabel: Record<string, string> = {
  queued: "等待执行",
  running: "进行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  pending: "等待",
};

export function JobResult({
  jobId,
  onLoaded,
  renderSample,
}: {
  jobId: string;
  onLoaded?: (job: Job, samples: Sample[]) => void;
  renderSample?: (sample: Sample, index: number) => React.ReactNode;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState("");

  useEffect(() => {
    setJob(null);
    setSamples([]);
    setIndex(0);
    if (!jobId) return;
    let stopped = false;
    async function poll() {
      try {
        const next = await getJob(jobId);
        if (stopped) return;
        setJob(next);
        if (["succeeded", "failed"].includes(next.status)) {
          const rows = await getSamples(jobId);
          if (!stopped) {
            setSamples(rows);
            onLoaded?.(next, rows);
          }
          return;
        }
        window.setTimeout(poll, 700);
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : "任务读取失败");
      }
    }
    void poll();
    return () => {
      stopped = true;
    };
  }, [jobId, onLoaded]);

  async function download(format: string) {
    setExporting(format);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/v2/jobs/${jobId}/export`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ format }),
      });
      if (!response.ok) {
        const detail = await response.json();
        throw new Error(detail.detail || "导出失败");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/);
      link.download = match?.[1] || `samples-${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setExporting("");
    }
  }

  if (!jobId) return null;
  if (!job) return <section className="panel">正在读取任务…</section>;

  return (
    <section className="panel result-panel">
      <div className="section-heading">
        <div>
          <h2>本次结果</h2>
          <p>{statusLabel[job.status] || job.status}</p>
        </div>
        <span className={`status-badge ${job.status}`}>
          {statusLabel[job.status] || job.status}
        </span>
      </div>
      <div className="stage-list">
        {(job.stages || []).map((stage) => (
          <div className="stage-row" key={stage.id}>
            <span className={`stage-mark ${stage.status}`} />
            <strong>{stage.label}</strong>
            <span>{statusLabel[stage.status] || stage.status}</span>
          </div>
        ))}
      </div>
      {job.error && <div className="notice error">{job.error}</div>}
      {samples.length > 0 && (
        <>
          <div className="sample-toolbar">
            <strong>样本预览</strong>
            <span>
              {index + 1} / {samples.length}
            </span>
            <button className="button small" disabled={index === 0} onClick={() => setIndex(index - 1)}>
              上一条
            </button>
            <button
              className="button small"
              disabled={index >= samples.length - 1}
              onClick={() => setIndex(index + 1)}
            >
              下一条
            </button>
            <button
              className="button small"
              onClick={() => setIndex(Math.floor(Math.random() * samples.length))}
            >
              随机
            </button>
          </div>
          {renderSample ? (
            renderSample(samples[index], index)
          ) : (
            <EditableSample
              key={samples[index].id}
              sample={samples[index]}
              onSaved={(next) =>
                setSamples((current) =>
                  current.map((item) => (item.id === next.id ? next : item)),
                )
              }
            />
          )}
          <div className="export-row">
            <strong>导出当前任务</strong>
            {["jsonl", "alpaca", "openai-ft", "chatml", "huggingface", "csv"].map(
              (format) => (
                <button
                  className="button small"
                  disabled={Boolean(exporting)}
                  key={format}
                  onClick={() => void download(format)}
                >
                  {exporting === format ? "处理中…" : format}
                </button>
              ),
            )}
          </div>
        </>
      )}
      {job.status === "succeeded" && !samples.length && (
        <div className="notice">任务已完成，但没有生成可审核样本。</div>
      )}
      {error && <div className="notice error">{error}</div>}
    </section>
  );
}

function EditableSample({
  sample,
  onSaved,
}: {
  sample: Sample;
  onSaved: (sample: Sample) => void;
}) {
  const [question, setQuestion] = useState(sample.question);
  const [answer, setAnswer] = useState(sample.answer);
  const [reasoning, setReasoning] = useState(sample.reasoning);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(humanStatus = sample.quality.human_status || "pending") {
    setBusy(true);
    setMessage("");
    try {
      const next = await patchSample(sample.id, {
        question,
        answer,
        reasoning,
        quality: { human_status: humanStatus },
      });
      onSaved(next);
      setMessage(humanStatus === "confirmed" ? "已确认" : humanStatus === "rejected" ? "已驳回" : "修改已保存");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="sample-card editable">
      <label>
        问题
        <textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
      </label>
      <label>
        答案
        <textarea rows={5} value={answer} onChange={(e) => setAnswer(e.target.value)} />
      </label>
      <label>
        思维链
        <textarea rows={5} value={reasoning} onChange={(e) => setReasoning(e.target.value)} />
      </label>
      <div className="sample-meta">
        <span>来源：{String(sample.source.file || "未标注")}</span>
        <span>评分：{sample.quality.model_score || "未评分"}</span>
        <span>审核：{sample.quality.human_status || "待审核"}</span>
      </div>
      <div className="review-actions">
        <button className="button small" disabled={busy} onClick={() => void save()}>保存修改</button>
        <button className="button small approve" disabled={busy} onClick={() => void save("confirmed")}>确认样本</button>
        <button className="button small reject" disabled={busy} onClick={() => void save("rejected")}>驳回样本</button>
        {message && <span>{message}</span>}
      </div>
    </article>
  );
}
