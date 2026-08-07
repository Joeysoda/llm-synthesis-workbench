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

const toolLabels: Record<string, string> = {
  "synthetic-data-kit": "Synthetic Data Kit",
  "easy-dataset": "Easy Dataset",
  synlogic: "SynLogic",
  kaqg: "KAQG",
  cleanlab: "Cleanlab",
  legacy: "历史实验",
};

export default function CleanlabPage() {
  const workspace = useToolProject("cleanlab");
  const [sourceType, setSourceType] = useState<"asset" | "job">("asset");
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const [probabilityAssetIds, setProbabilityAssetIds] = useState<string[]>([]);
  const [sourceJobId, setSourceJobId] = useState("");
  const [sourceJobs, setSourceJobs] = useState<Job[]>([]);
  const [idColumn, setIdColumn] = useState("id");
  const [textColumn, setTextColumn] = useState("text");
  const [labelColumn, setLabelColumn] = useState("label");
  const [jobId, setJobId] = useState("");
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<{ services: { cleanlab?: { status: string } } }>(
        "/api/v2/integrations/status",
      ),
      api<Job[]>("/api/v2/jobs?status=succeeded&limit=200"),
    ])
      .then(([status, jobs]) => {
        setReady(status.services.cleanlab?.status === "available");
        setSourceJobs(
          jobs.filter(
            (job) =>
              job.workflow_type !== "text-classification-audit" &&
              job.workflow_type !== "arrow-maze-verify",
          ),
        );
      })
      .catch(() => setReady(false));
  }, []);

  useEffect(() => {
    setSummary(null);
    if (!workspace.project) {
      setJobId("");
      return;
    }
    api<Job[]>(`/api/v2/jobs?project_id=${workspace.project.id}&limit=20`)
      .then((jobs) => {
        const latest = jobs.find((job) => job.workflow_type === "text-classification-audit");
        setJobId(latest?.id || "");
      })
      .catch(() => setJobId(""));
  }, [workspace.project]);

  async function run() {
    if (!workspace.project) return;
    setBusy(true);
    setError("");
    setSummary(null);
    try {
      const job = await api<Job>(
        `/api/v2/cleanlab/projects/${workspace.project.id}/jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            source_type: sourceType,
            asset_id: sourceType === "asset" ? assetIds[0] || null : null,
            source_job_id: sourceType === "job" ? sourceJobId || null : null,
            pred_probs_asset_id:
              sourceType === "asset" ? probabilityAssetIds[0] || null : null,
            id_column: idColumn.trim(),
            text_column: textColumn.trim(),
            label_column: labelColumn.trim(),
            confirmed: true,
          }),
        },
      );
      setJobId(job.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cleanlab 任务启动失败");
    } finally {
      setBusy(false);
    }
  }

  const canRun =
    ready &&
    !busy &&
    (sourceType === "asset"
      ? Boolean(assetIds[0] && idColumn.trim() && textColumn.trim() && labelColumn.trim())
      : Boolean(sourceJobId));

  return (
    <div className="page">
      <ProjectBar
        toolId="cleanlab"
        title="Cleanlab"
        subtitle="在本机检查错标签、异常样本以及完全重复和近重复数据。"
        connection={{
          label: ready ? "Cleanlab 本机环境可用" : "Cleanlab 依赖未就绪",
          tone: ready ? "ok" : "bad",
        }}
        workspace={workspace}
      />
      {workspace.project ? (
        <>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>选择待检查数据</h2>
                <p>上传分类数据可以检查标签；导入平台任务时只检查异常与近重复。</p>
              </div>
              <span className="local-badge">完全本机运行</span>
            </div>
            <div className="source-tabs" role="tablist" aria-label="Cleanlab 输入方式">
              <button
                className={sourceType === "asset" ? "active" : ""}
                role="tab"
                aria-selected={sourceType === "asset"}
                onClick={() => setSourceType("asset")}
              >
                上传分类数据
              </button>
              <button
                className={sourceType === "job" ? "active" : ""}
                role="tab"
                aria-selected={sourceType === "job"}
                onClick={() => setSourceType("job")}
              >
                导入已完成任务
              </button>
            </div>
            {sourceType === "asset" ? (
              <div className="cleanlab-inputs">
                <div>
                  <h3>数据文件</h3>
                  <AssetPicker
                    projectId={workspace.project.id}
                    selected={assetIds}
                    onChange={setAssetIds}
                    accept=".csv,.json,.jsonl"
                    multiple={false}
                    hint="支持 20～5000 条 CSV、JSON 或 JSONL"
                  />
                </div>
                <div>
                  <h3>预测概率（可选）</h3>
                  <AssetPicker
                    projectId={workspace.project.id}
                    selected={probabilityAssetIds}
                    onChange={setProbabilityAssetIds}
                    accept=".json,.jsonl"
                    multiple={false}
                    hint="不上传时自动用本机交叉验证生成概率"
                  />
                </div>
                <div className="form-grid three">
                  <label>
                    ID 列
                    <input value={idColumn} onChange={(event) => setIdColumn(event.target.value)} />
                  </label>
                  <label>
                    文本列
                    <input value={textColumn} onChange={(event) => setTextColumn(event.target.value)} />
                  </label>
                  <label>
                    标签列
                    <input value={labelColumn} onChange={(event) => setLabelColumn(event.target.value)} />
                  </label>
                </div>
              </div>
            ) : (
              <div className="job-source-picker">
                <label>
                  已完成任务
                  <select value={sourceJobId} onChange={(event) => setSourceJobId(event.target.value)}>
                    <option value="">请选择一个任务</option>
                    {sourceJobs.map((job) => (
                      <option value={job.id} key={job.id}>
                        {toolLabels[job.tool_id || ""] || job.tool_name} · {job.project_name || "历史项目"} · {new Date(job.created_at).toLocaleString("zh-CN")}
                      </option>
                    ))}
                  </select>
                </label>
                {!sourceJobs.length && (
                  <div className="notice">暂时没有可导入的已完成任务。</div>
                )}
                <p className="field-hint">
                  平台生成样本通常没有分类标签，因此不会显示“错标签”结论。
                </p>
              </div>
            )}
            <div className="run-row">
              <button className="button primary large" disabled={!canRun} onClick={() => void run()}>
                {busy ? "正在创建任务…" : "开始检查数据质量"}
              </button>
              <span className="field-hint">不会自动改写标签，所有建议都需要人工确认。</span>
            </div>
            {error && <div className="notice error">{error}</div>}
          </section>
          {summary && <CleanlabSummary value={summary} />}
          <JobResult
            jobId={jobId}
            exportFormats={["csv", "jsonl"]}
            onLoaded={(job) => setSummary((job.result?.data || job.result || null) as Record<string, unknown> | null)}
            renderSample={(sample, _index, onUpdated) => (
              <CleanlabSample sample={sample} onUpdated={onUpdated} />
            )}
            resultActions={(job) => (
              <a className="button small" href={artifactUrl(job.id, "cleanlab-audit")} download>
                审计报告
              </a>
            )}
          />
        </>
      ) : (
        <section className="empty-state">
          <h2>先建立一个 Cleanlab 项目</h2>
          <p>输入文件、检查分数、人工决策和清洗后的导出会保存在这个独立项目中。</p>
        </section>
      )}
    </div>
  );
}

function CleanlabSummary({ value }: { value: Record<string, unknown> }) {
  return (
    <section className="panel compact-panel">
      <div className="section-heading">
        <div>
          <h2>检查摘要</h2>
          <p>{value.has_labels ? "已启用标签、异常和近重复检查。" : "导入数据没有分类标签，仅启用异常和近重复检查。"}</p>
        </div>
      </div>
      <div className="result-metrics">
        <SummaryMetric label="数据量" value={value.row_count} />
        <SummaryMetric label="可疑标签" value={value.has_labels ? value.label_issues : "不适用"} />
        <SummaryMetric label="异常样本" value={value.outliers} />
        <SummaryMetric label="近重复" value={value.near_duplicates} />
      </div>
    </section>
  );
}

function SummaryMetric({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value == null ? "—" : String(value)}</strong>
    </div>
  );
}

function CleanlabSample({
  sample,
  onUpdated,
}: {
  sample: Sample;
  onUpdated: (sample: Sample) => void;
}) {
  const [manualLabel, setManualLabel] = useState(sample.answer);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    setManualLabel(sample.answer);
    setMessage("");
  }, [sample.id, sample.answer]);

  async function decide(
    decision: "accept_suggestion" | "keep_original" | "manual",
  ) {
    setBusy(decision);
    setMessage("");
    try {
      const pathId = sample.id.split("/").map(encodeURIComponent).join("/");
      const next = await api<Sample>(
        `/api/v2/cleanlab/samples/${pathId}/decision`,
        {
          method: "PATCH",
          body: JSON.stringify({
            decision,
            corrected_label: decision === "manual" ? manualLabel.trim() : null,
          }),
        },
      );
      onUpdated(next);
      setManualLabel(next.answer);
      setMessage("审核决定已保存");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "审核保存失败");
    } finally {
      setBusy("");
    }
  }

  const quality = sample.quality;
  const original = String(quality.original_label ?? sample.source.original_label ?? "");
  const suggested = String(quality.suggested_label || "");
  const current = String(quality.current_label || sample.answer || "");

  return (
    <article className="sample-card cleanlab-sample">
      <div className="issue-heading">
        <span className={quality.is_label_issue ? "issue-tag warning" : "issue-tag"}>
          {quality.is_label_issue ? "可疑标签" : "标签未标记"}
        </span>
        <span className={quality.is_outlier_issue ? "issue-tag warning" : "issue-tag"}>
          {quality.is_outlier_issue ? "异常样本" : "非异常"}
        </span>
        <span className={quality.is_near_duplicate_issue ? "issue-tag warning" : "issue-tag"}>
          {quality.is_near_duplicate_issue ? "近重复" : "未发现近重复"}
        </span>
      </div>
      <div className="audit-text">
        <strong>文本</strong>
        <p>{sample.question}</p>
        <small>{sample.reasoning}</small>
      </div>
      <div className="label-grid">
        <div><span>原标签</span><strong>{original || "无"}</strong></div>
        <div><span>建议标签</span><strong>{suggested || "无"}</strong></div>
        <div><span>当前标签</span><strong>{current || "无"}</strong></div>
        <div><span>审核决定</span><strong>{decisionLabel(String(quality.decision || "pending"))}</strong></div>
      </div>
      <div className="score-grid">
        <Score label="标签质量" value={quality.label_score} />
        <Score label="异常分数" value={quality.outlier_score} />
        <Score label="近重复分数" value={quality.near_duplicate_score} />
      </div>
      <div className="cleanlab-review">
        <button
          className="button small approve"
          disabled={Boolean(busy) || !suggested}
          onClick={() => void decide("accept_suggestion")}
        >
          {busy === "accept_suggestion" ? "保存中…" : "接受建议"}
        </button>
        <button
          className="button small"
          disabled={Boolean(busy) || !original}
          onClick={() => void decide("keep_original")}
        >
          {busy === "keep_original" ? "保存中…" : "保留原标签"}
        </button>
        <label>
          手动标签
          <input value={manualLabel} onChange={(event) => setManualLabel(event.target.value)} />
        </label>
        <button
          className="button small"
          disabled={Boolean(busy) || !manualLabel.trim()}
          onClick={() => void decide("manual")}
        >
          {busy === "manual" ? "保存中…" : "保存手动标签"}
        </button>
        {message && <span className={message.includes("失败") ? "form-error" : "ok-text"}>{message}</span>}
      </div>
    </article>
  );
}

function Score({ label, value }: { label: string; value?: number | null }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{typeof value === "number" ? value.toFixed(3) : "不适用"}</strong>
    </div>
  );
}

function decisionLabel(value: string) {
  const labels: Record<string, string> = {
    pending: "待审核",
    accept_suggestion: "已接受建议",
    keep_original: "保留原标签",
    manual: "手动修改",
  };
  return labels[value] || value;
}
