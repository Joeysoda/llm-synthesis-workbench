"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useMemo, useState } from "react";
import { api, type Job } from "../../lib/api";
import { AssetPicker } from "../components/AssetPicker";
import { JobResult } from "../components/JobResult";
import { ProjectBar, useToolProject } from "../components/ProjectBar";

const modes = [
  { id: "qa", label: "普通 QA", description: "从材料生成问题和答案" },
  { id: "cot", label: "CoT 思维链", description: "生成带推理过程的样本" },
  { id: "summary", label: "文档摘要", description: "为每份材料生成摘要" },
  { id: "cot-enhance", label: "补充 CoT", description: "为已有 JSON 对话补推理" },
  { id: "multimodal-qa", label: "多模态 QA", description: "从图文材料生成问答" },
] as const;

type Mode = (typeof modes)[number]["id"];

export default function SyntheticPage() {
  const workspace = useToolProject("synthetic-data-kit");
  const [mode, setMode] = useState<Mode>("cot");
  const [assets, setAssets] = useState<string[]>([]);
  const [count, setCount] = useState(30);
  const [threshold, setThreshold] = useState(7);
  const [chunkSize, setChunkSize] = useState(4000);
  const [overlap, setOverlap] = useState(200);
  const [advanced, setAdvanced] = useState(false);
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [textReady, setTextReady] = useState(false);
  const [visionReady, setVisionReady] = useState(false);
  const [statusMessage, setStatusMessage] = useState("正在检查连接");

  useEffect(() => {
    api<{
      text_model: { credential_state: string; message: string };
      vision_model: { credential_state: string };
      services: { synthetic_cli: { status: string } };
    }>("/api/v2/integrations/status")
      .then((value) => {
        const localReady = value.services.synthetic_cli.status === "available";
        const nextTextReady = value.text_model.credential_state === "verified";
        setTextReady(nextTextReady);
        setVisionReady(value.vision_model.credential_state === "configured");
        setStatusMessage(
          !localReady
            ? "Synthetic CLI 未找到"
            : nextTextReady
              ? "本地组件与文本模型可用"
              : value.text_model.message,
        );
      })
      .catch(() => setStatusMessage("无法读取连接状态"));
  }, []);

  useEffect(() => {
    if (!workspace.project) {
      setJobId("");
      return;
    }
    api<Job[]>(`/api/v2/jobs?project_id=${workspace.project.id}&limit=20`)
      .then((jobs) => {
        const latest = jobs.find((job) =>
          ["qa", "cot", "summary", "cot-enhance", "multimodal-qa"].includes(
            job.workflow_type,
          ),
        );
        setJobId(latest?.id || "");
      })
      .catch(() => setJobId(""));
  }, [workspace.project]);

  const disabledReason = useMemo(() => {
    if (!workspace.project) return "请先创建项目";
    if (!assets.length) return "请先选择输入文件";
    if (mode === "multimodal-qa" && !visionReady) return "视觉模型未配置";
    if (!textReady) return "文本模型尚未通过连接验证";
    return "";
  }, [assets.length, mode, textReady, visionReady, workspace.project]);

  async function run() {
    if (!workspace.project || disabledReason) return;
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>(
        `/api/v2/synthetic/projects/${workspace.project.id}/jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            workflow_type: mode,
            asset_ids: assets,
            num_pairs: count,
            min_retained: mode === "summary" ? 1 : Math.min(20, count),
            chunk_size: chunkSize,
            chunk_overlap: overlap,
            threshold,
            confirmed: true,
          }),
        },
      );
      setJobId(job.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务启动失败");
    } finally {
      setBusy(false);
    }
  }

  const accept =
    mode === "cot-enhance"
      ? ".json"
      : mode === "multimodal-qa"
        ? ".pdf,.zip,.png,.jpg,.jpeg,.webp"
        : ".md,.txt,.docx,.pdf,.epub";

  return (
    <div className="page">
      <ProjectBar
        toolId="synthetic-data-kit"
        title="Synthetic Data Kit"
        subtitle="将文档或已有对话转换成可训练、可筛选、可导出的合成数据。"
        connection={{
          label: statusMessage,
          tone: textReady ? "ok" : "warn",
        }}
        workspace={workspace}
      />
      {workspace.project ? (
        <>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>1. 选择生成功能</h2>
                <p>每次任务会自动完成解析、生成、筛选和格式整理。</p>
              </div>
            </div>
            <div className="mode-grid five">
              {modes.map((item) => (
                <button
                  key={item.id}
                  className={mode === item.id ? "mode-card selected" : "mode-card"}
                  onClick={() => {
                    setMode(item.id);
                    setAssets([]);
                    setJobId("");
                    if (item.id === "cot-enhance") setCount(1);
                    if (item.id === "summary") setCount(1);
                  }}
                >
                  <strong>{item.label}</strong>
                  <span>{item.description}</span>
                  {item.id === "multimodal-qa" && !visionReady && (
                    <small>视觉模型未配置</small>
                  )}
                </button>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>2. 选择输入</h2>
                <p>
                  {mode === "cot-enhance"
                    ? "上传包含 conversations 或 qa_pairs 的 JSON 文件。"
                    : "可一次上传并选择多份材料，文件始终保存在当前项目中。"}
                </p>
              </div>
            </div>
            <AssetPicker
              projectId={workspace.project.id}
              selected={assets}
              onChange={setAssets}
              accept={accept}
              hint={mode === "cot-enhance" ? "仅支持 JSON" : "单文件不超过 25 MB"}
            />
            {mode === "cot-enhance" && (
              <div className="format-guide">
                <strong>JSON 最小格式示例</strong>
                <p>把已有问题和答案放在 qa_pairs 中，系统会为回答补充推理过程。</p>
                <pre>{`{
  "qa_pairs": [
    {
      "question": "为什么数据清洗很重要？",
      "answer": "它可以减少错误、重复和噪声样本。"
    }
  ]
}`}</pre>
              </div>
            )}
          </section>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>3. 设置数量并开始</h2>
                <p>摘要模式按文件生成；其他模式按目标样本数生成。</p>
              </div>
              <button className="text-button" onClick={() => setAdvanced(!advanced)}>
                {advanced ? "收起高级设置" : "高级设置"}
              </button>
            </div>
            <div className="form-grid">
              <label>
                {mode === "cot-enhance" ? "最多处理数量" : "目标数量"}
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={count}
                  disabled={mode === "summary"}
                  onChange={(event) => setCount(Number(event.target.value))}
                />
              </label>
              <label>
                生成功能
                <input readOnly value={modes.find((item) => item.id === mode)?.label} />
              </label>
            </div>
            {advanced && (
              <div className="form-grid advanced-fields">
                <label>
                  分块大小
                  <input
                    type="number"
                    value={chunkSize}
                    onChange={(event) => setChunkSize(Number(event.target.value))}
                  />
                </label>
                <label>
                  分块重叠
                  <input
                    type="number"
                    value={overlap}
                    onChange={(event) => setOverlap(Number(event.target.value))}
                  />
                </label>
                <label>
                  筛选阈值
                  <input
                    type="number"
                    min={1}
                    max={10}
                    step={0.5}
                    value={threshold}
                    onChange={(event) => setThreshold(Number(event.target.value))}
                  />
                </label>
                <label>
                  模型
                  <input readOnly value="使用设置中的文本模型" />
                </label>
              </div>
            )}
            <div className="run-row">
              <button
                className="button primary large"
                disabled={Boolean(disabledReason) || busy}
                onClick={() => void run()}
              >
                {busy ? "正在创建任务…" : "开始生成"}
              </button>
              <span className={disabledReason ? "form-error" : "field-hint"}>
                {disabledReason || `将处理 ${assets.length} 个文件`}
              </span>
            </div>
            {error && <div className="notice error">{error}</div>}
          </section>
          <JobResult jobId={jobId} />
        </>
      ) : (
        <section className="empty-state">
          <h2>先建立一个 Synthetic 项目</h2>
          <p>项目用于隔离文件、任务、样本和导出结果，不会与另外两个工具混用。</p>
        </section>
      )}
    </div>
  );
}
