"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useMemo, useState } from "react";
import { api, getJob, type Job } from "../../lib/api";
import { AssetPicker } from "../components/AssetPicker";
import { JobResult } from "../components/JobResult";
import { ProjectBar, useToolProject } from "../components/ProjectBar";

const tabs = [
  { id: "document-qa", label: "文档问答" },
  { id: "distillation", label: "数据蒸馏" },
  { id: "image-qa", label: "图片问答" },
  { id: "evaluation", label: "评估数据" },
] as const;
type Workflow = (typeof tabs)[number]["id"];

const questionTypes = [
  { id: "true-false", label: "判断题" },
  { id: "single-choice", label: "单选题" },
  { id: "multiple-choice", label: "多选题" },
  { id: "short-answer", label: "简答题" },
  { id: "open", label: "开放题" },
];

export default function EasyDatasetPage() {
  const workspace = useToolProject("easy-dataset");
  const [workflow, setWorkflow] = useState<Workflow>("document-qa");
  const [assets, setAssets] = useState<string[]>([]);
  const [count, setCount] = useState(20);
  const [conversation, setConversation] = useState("single");
  const [gaExpansion, setGaExpansion] = useState(false);
  const [topic, setTopic] = useState("");
  const [depth, setDepth] = useState(2);
  const [tagsPerLevel, setTagsPerLevel] = useState(3);
  const [questionsPerTag, setQuestionsPerTag] = useState(3);
  const [types, setTypes] = useState(["single-choice", "short-answer"]);
  const [questionsPerType, setQuestionsPerType] = useState(3);
  const [questionsPerImage, setQuestionsPerImage] = useState(3);
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [serviceReady, setServiceReady] = useState(false);
  const [textReady, setTextReady] = useState(false);
  const [visionReady, setVisionReady] = useState(false);
  const [connection, setConnection] = useState("正在检查连接");

  useEffect(() => {
    api<{
      text_model: { credential_state: string; message: string };
      vision_model: { credential_state: string };
      services: { easy_dataset: { status: string } };
    }>("/api/v2/integrations/status")
      .then((value) => {
        const sidecar = value.services.easy_dataset.status === "reachable";
        const text = value.text_model.credential_state === "verified";
        setServiceReady(sidecar);
        setTextReady(text);
        setVisionReady(value.vision_model.credential_state === "configured");
        setConnection(
          !sidecar
            ? "Easy Dataset 服务未启动"
            : !text
              ? value.text_model.message
              : "sidecar 与文本模型已就绪",
        );
      })
      .catch(() => setConnection("无法读取连接状态"));
  }, []);

  useEffect(() => {
    if (!workspace.project) {
      setJobId("");
      return;
    }
    api<Job[]>(`/api/v2/jobs?project_id=${workspace.project.id}&limit=30`)
      .then((jobs) => {
        const latest = jobs.find((job) =>
          ["document-qa", "distillation", "image-qa", "evaluation"].includes(
            job.workflow_type,
          ),
        );
        setJobId(latest?.id || "");
      })
      .catch(() => setJobId(""));
  }, [workspace.project]);

  const estimate = useMemo(() => {
    if (workflow === "distillation")
      return Math.pow(tagsPerLevel, depth) * questionsPerTag;
    if (workflow === "evaluation") return types.length * questionsPerType;
    return count;
  }, [
    count,
    depth,
    questionsPerTag,
    questionsPerType,
    tagsPerLevel,
    types.length,
    workflow,
  ]);

  const disabledReason = useMemo(() => {
    if (!workspace.project) return "请先创建项目";
    if (!serviceReady) return "Easy Dataset 服务未启动";
    if (workflow === "image-qa" && !visionReady) return "视觉模型未配置";
    if (workflow !== "image-qa" && !textReady) return "文本模型尚未完成轮换确认";
    if (["document-qa", "image-qa", "evaluation"].includes(workflow) && !assets.length)
      return "请先选择输入文件";
    if (workflow === "distillation" && !topic.trim()) return "请输入领域主题";
    if (workflow === "evaluation" && !types.length) return "请至少选择一种题型";
    return "";
  }, [
    assets.length,
    serviceReady,
    textReady,
    topic,
    types.length,
    visionReady,
    workflow,
    workspace.project,
  ]);

  async function run() {
    if (!workspace.project || disabledReason) return;
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>(
        `/api/v2/easy-dataset/projects/${workspace.project.id}/jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            workflow_type: workflow,
            asset_ids: assets,
            target_count: estimate,
            conversation_mode: conversation,
            ga_expansion: gaExpansion,
            topic,
            tag_depth: depth,
            tags_per_level: tagsPerLevel,
            questions_per_tag: questionsPerTag,
            question_types: types,
            questions_per_type: questionsPerType,
            questions_per_image: questionsPerImage,
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

  return (
    <div className="page">
      <ProjectBar
        toolId="easy-dataset"
        title="Easy Dataset"
        subtitle="通过未修改的本地 sidecar 构建文档、领域、图片和评估数据。"
        connection={{
          label: connection,
          tone: serviceReady && textReady ? "ok" : "warn",
        }}
        workspace={workspace}
      />
      {workspace.project ? (
        <>
          <div className="tab-bar" role="tablist" aria-label="Easy Dataset 功能">
            {tabs.map((tab) => (
              <button
                role="tab"
                aria-selected={workflow === tab.id}
                className={workflow === tab.id ? "active" : ""}
                key={tab.id}
                onClick={() => {
                  setWorkflow(tab.id);
                  setAssets([]);
                  setJobId("");
                  setError("");
                }}
              >
                {tab.label}
                {tab.id === "image-qa" && !visionReady && <small>未配置</small>}
              </button>
            ))}
          </div>
          {workflow === "document-qa" && (
            <DocumentQA
              projectId={workspace.project.id}
              assets={assets}
              setAssets={setAssets}
              count={count}
              setCount={setCount}
              conversation={conversation}
              setConversation={setConversation}
              gaExpansion={gaExpansion}
              setGaExpansion={setGaExpansion}
            />
          )}
          {workflow === "distillation" && (
            <Distillation
              topic={topic}
              setTopic={setTopic}
              depth={depth}
              setDepth={setDepth}
              tagsPerLevel={tagsPerLevel}
              setTagsPerLevel={setTagsPerLevel}
              questionsPerTag={questionsPerTag}
              setQuestionsPerTag={setQuestionsPerTag}
              estimate={estimate}
              conversation={conversation}
              setConversation={setConversation}
            />
          )}
          {workflow === "image-qa" && (
            <ImageQA
              projectId={workspace.project.id}
              assets={assets}
              setAssets={setAssets}
              questionsPerImage={questionsPerImage}
              setQuestionsPerImage={setQuestionsPerImage}
              visionReady={visionReady}
            />
          )}
          {workflow === "evaluation" && (
            <Evaluation
              projectId={workspace.project.id}
              assets={assets}
              setAssets={setAssets}
              types={types}
              setTypes={setTypes}
              questionsPerType={questionsPerType}
              setQuestionsPerType={setQuestionsPerType}
            />
          )}
          <section className="panel action-panel">
            <div>
              <h2>开始生成</h2>
              <p>
                预计生成 {estimate} 条；启动后可在本页直接查看结果。
              </p>
            </div>
            <button
              className="button primary large"
              disabled={Boolean(disabledReason) || busy}
              onClick={() => void run()}
            >
              {busy ? "正在创建任务…" : "开始生成"}
            </button>
            <span className={disabledReason ? "form-error" : "field-hint"}>
              {disabledReason || "参数已就绪"}
            </span>
          </section>
          {error && <div className="notice error">{error}</div>}
          <JobResult jobId={jobId} />
        </>
      ) : (
        <section className="empty-state">
          <h2>先建立一个 Easy Dataset 项目</h2>
          <p>文档、sidecar 项目、生成记录和审核结果都会固定在当前项目内。</p>
        </section>
      )}
    </div>
  );
}

function DocumentQA(props: {
  projectId: string;
  assets: string[];
  setAssets: (value: string[]) => void;
  count: number;
  setCount: (value: number) => void;
  conversation: string;
  setConversation: (value: string) => void;
  gaExpansion: boolean;
  setGaExpansion: (value: boolean) => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const [chunks, setChunks] = useState<{ id?: string; content?: string; name?: string }[]>([]);
  const [previewError, setPreviewError] = useState("");

  async function preview() {
    if (!props.assets[0]) return;
    setPreviewing(true);
    setPreviewError("");
    setChunks([]);
    try {
      const job = await api<Job>(
        `/api/v2/easy-dataset/projects/${props.projectId}/preview`,
        {
          method: "POST",
          body: JSON.stringify({ asset_id: props.assets[0] }),
        },
      );
      let current = job;
      while (!["succeeded", "failed"].includes(current.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        current = await getJob(job.id);
      }
      if (current.status === "failed") throw new Error(current.error || "文档分块失败");
      const data = (current.result?.data || {}) as { chunks?: { id?: string; content?: string; name?: string }[] };
      setChunks(data.chunks || []);
    } catch (reason) {
      setPreviewError(reason instanceof Error ? reason.message : "文档分块失败");
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>文档问答</h2>
          <p>上传材料，选择数据形态，然后生成问题、答案和思维链。</p>
        </div>
      </div>
      <AssetPicker
        projectId={props.projectId}
        selected={props.assets}
        onChange={props.setAssets}
        accept=".pdf,.docx,.txt,.md,.epub"
        hint="支持 PDF、DOCX、TXT、Markdown、EPUB"
      />
      <div className="preview-row">
        <button
          className="button small"
          disabled={!props.assets.length || previewing}
          onClick={() => void preview()}
        >
          {previewing ? "正在分块…" : "导入并预览文本块"}
        </button>
        <span className="field-hint">预览只调用本地 sidecar，不调用文本模型。</span>
      </div>
      {chunks.length > 0 && (
        <div className="chunk-preview">
          <strong>文本块预览（{chunks.length}）</strong>
          {chunks.slice(0, 5).map((chunk, index) => (
            <details key={chunk.id || index}>
              <summary>{chunk.name || `文本块 ${index + 1}`}</summary>
              <p>{chunk.content || "该接口未返回正文预览"}</p>
            </details>
          ))}
        </div>
      )}
      {previewError && <div className="notice error">{previewError}</div>}
      <div className="form-grid three top-space">
        <label>
          数据形态
          <select value={props.conversation} onChange={(e) => props.setConversation(e.target.value)}>
            <option value="single">单轮问答</option>
            <option value="multi">多轮对话</option>
            <option value="both">两者都生成</option>
          </select>
        </label>
        <label>
          目标数量
          <input
            type="number"
            min={1}
            max={500}
            value={props.count}
            onChange={(e) => props.setCount(Number(e.target.value))}
          />
        </label>
        <label>
          分块方式
          <select defaultValue="smart">
            <option value="smart">智能分块</option>
            <option value="paragraph">按段落</option>
            <option value="size">按长度</option>
          </select>
        </label>
      </div>
      <label className="check-line">
        <input
          type="checkbox"
          checked={props.gaExpansion}
          onChange={(e) => props.setGaExpansion(e.target.checked)}
        />
        开启 GA 文体—受众扩增，让少量文本生成更多样的问题表达
      </label>
    </section>
  );
}

function Distillation(props: {
  topic: string;
  setTopic: (value: string) => void;
  depth: number;
  setDepth: (value: number) => void;
  tagsPerLevel: number;
  setTagsPerLevel: (value: number) => void;
  questionsPerTag: number;
  setQuestionsPerTag: (value: number) => void;
  estimate: number;
  conversation: string;
  setConversation: (value: string) => void;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>数据蒸馏</h2>
          <p>不上传文档，从领域主题生成标签树，再沿叶子标签生成问答。</p>
        </div>
      </div>
      <label className="wide-field">
        领域主题
        <input
          value={props.topic}
          onChange={(e) => props.setTopic(e.target.value)}
          placeholder="例如：大模型数据安全治理"
        />
      </label>
      <div className="form-grid four">
        <label>
          标签层级
          <input type="number" min={1} max={5} value={props.depth} onChange={(e) => props.setDepth(Number(e.target.value))} />
        </label>
        <label>
          每层标签数
          <input type="number" min={1} max={20} value={props.tagsPerLevel} onChange={(e) => props.setTagsPerLevel(Number(e.target.value))} />
        </label>
        <label>
          每个叶子问题数
          <input type="number" min={1} max={50} value={props.questionsPerTag} onChange={(e) => props.setQuestionsPerTag(Number(e.target.value))} />
        </label>
        <label>
          数据形态
          <select value={props.conversation} onChange={(e) => props.setConversation(e.target.value)}>
            <option value="single">单轮问答</option>
            <option value="multi">多轮对话</option>
            <option value="both">两者都生成</option>
          </select>
        </label>
      </div>
      <div className="estimate-box">
        预计生成 <strong>{props.estimate}</strong> 条
        <span>计算方式：{props.tagsPerLevel}^{props.depth} × {props.questionsPerTag}</span>
        {props.estimate > 100 && <em>数量较大，建议先缩小参数验证。</em>}
      </div>
    </section>
  );
}

function ImageQA(props: {
  projectId: string;
  assets: string[];
  setAssets: (value: string[]) => void;
  questionsPerImage: number;
  setQuestionsPerImage: (value: number) => void;
  visionReady: boolean;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>图片问答</h2>
          <p>导入图片、PDF 或 ZIP；视觉模型未配置时只保留输入，不发送生成请求。</p>
        </div>
        <span className={`status-badge ${props.visionReady ? "succeeded" : "failed"}`}>
          {props.visionReady ? "视觉模型已配置" : "视觉模型未配置"}
        </span>
      </div>
      <AssetPicker
        projectId={props.projectId}
        selected={props.assets}
        onChange={props.setAssets}
        accept=".png,.jpg,.jpeg,.webp,.pdf,.zip"
        hint="支持图片、PDF、ZIP"
      />
      <div className="form-grid three top-space">
        <label>
          每张图片问题数
          <input
            type="number"
            min={1}
            max={30}
            value={props.questionsPerImage}
            onChange={(e) => props.setQuestionsPerImage(Number(e.target.value))}
          />
        </label>
        <label>
          语言
          <select defaultValue="中文">
            <option>中文</option>
            <option>英文</option>
          </select>
        </label>
        <label>
          视觉模型
          <input readOnly value={props.visionReady ? "使用设置中的视觉模型" : "未配置"} />
        </label>
      </div>
    </section>
  );
}

function Evaluation(props: {
  projectId: string;
  assets: string[];
  setAssets: (value: string[]) => void;
  types: string[];
  setTypes: (value: string[]) => void;
  questionsPerType: number;
  setQuestionsPerType: (value: number) => void;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>评估数据</h2>
          <p>从文档文本块生成多种题型，并使用 Judge 模型进行质量评分。</p>
        </div>
      </div>
      <AssetPicker
        projectId={props.projectId}
        selected={props.assets}
        onChange={props.setAssets}
        accept=".pdf,.docx,.txt,.md,.epub"
        multiple={false}
        hint="第一版每次选择一份文档"
      />
      <div className="choice-group top-space">
        <span>题型</span>
        {questionTypes.map((item) => (
          <label className="check-line" key={item.id}>
            <input
              type="checkbox"
              checked={props.types.includes(item.id)}
              onChange={(event) =>
                props.setTypes(
                  event.target.checked
                    ? [...props.types, item.id]
                    : props.types.filter((value) => value !== item.id),
                )
              }
            />
            {item.label}
          </label>
        ))}
      </div>
      <label className="short-field">
        每类数量
        <input
          type="number"
          min={1}
          max={30}
          value={props.questionsPerType}
          onChange={(e) => props.setQuestionsPerType(Number(e.target.value))}
        />
      </label>
    </section>
  );
}
