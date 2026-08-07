"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Job, type Project, type ToolId } from "../lib/api";

type IntegrationStatus = {
  text_model: {
    credential_state: string;
    model: string;
    message: string;
  };
  vision_model: {
    credential_state: string;
    model: string;
    message: string;
  };
  services: Record<
    string,
    { status: string; endpoint?: string; version?: string; message?: string }
  >;
};

const cards: {
  id: ToolId;
  title: string;
  description: string;
  features: string[];
  href: string;
  serviceKey: string;
  needsText: boolean;
  needsVision: boolean;
}[] = [
  {
    id: "synthetic-data-kit",
    title: "Synthetic Data Kit",
    description: "适合把文档批量转换成训练数据，完整执行解析、生成、质量筛选和格式转换。",
    features: ["普通 QA", "CoT 思维链", "摘要与 CoT 增强"],
    href: "/synthetic",
    serviceKey: "synthetic_cli",
    needsText: true,
    needsVision: true,
  },
  {
    id: "easy-dataset",
    title: "Easy Dataset",
    description: "适合面向文档和领域主题构建问答、蒸馏数据、图片问答与评估数据。",
    features: ["文档问答", "数据蒸馏", "图片与评估数据"],
    href: "/easy-dataset",
    serviceKey: "easy_dataset",
    needsText: true,
    needsVision: true,
  },
  {
    id: "synlogic",
    title: "SynLogic",
    description: "适合生成可被规则验证的 Arrow Maze 逻辑题，并逐条检查答案是否正确。",
    features: ["迷宫生成", "确定性验证", "原始与统一 JSONL"],
    href: "/synlogic",
    serviceKey: "synlogic",
    needsText: false,
    needsVision: false,
  },
  {
    id: "kaqg",
    title: "KAQG",
    description: "适合从 PDF 构建知识图谱，并生成带来源证据和难度评估的单选题。",
    features: ["知识图谱", "难度可控单选题", "模型评估与证据"],
    href: "/kaqg",
    serviceKey: "kaqg_worker",
    needsText: true,
    needsVision: false,
  },
  {
    id: "cleanlab",
    title: "Cleanlab",
    description: "适合在本机发现分类数据中的错标签、异常样本和重复内容。",
    features: ["错标签检查", "异常检测", "近重复与人工审核"],
    href: "/cleanlab",
    serviceKey: "cleanlab",
    needsText: false,
    needsVision: false,
  },
];

const workflowLabels: Record<string, string> = {
  qa: "普通 QA",
  cot: "CoT 思维链",
  summary: "文档摘要",
  "cot-enhance": "补充 CoT",
  "multimodal-qa": "多模态 QA",
  "document-qa": "文档问答",
  "document-preview": "文档分块预览",
  distillation: "数据蒸馏",
  "image-qa": "图片问答",
  evaluation: "评估数据",
  "arrow-maze": "Arrow Maze",
  "knowledge-graph-scq": "知识图谱单选题",
  "text-classification-audit": "文本分类质量检查",
};

export default function OverviewPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [projects, setProjects] = useState<Record<string, Project[]>>({});
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [nextStatus, nextJobs, ...projectLists] = await Promise.all([
          api<IntegrationStatus>("/api/v2/integrations/status"),
          api<Job[]>("/api/v2/jobs?status=succeeded&artifacts_only=true&limit=30"),
          ...cards.map((card) =>
            api<Project[]>(`/api/v2/tools/${card.id}/projects`),
          ),
        ]);
        setStatus(nextStatus);
        setJobs(nextJobs);
        setProjects(
          Object.fromEntries(
            cards.map((card, index) => [card.id, projectLists[index]]),
          ),
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法连接本机网关");
      }
    }
    void load();
  }, []);

  return (
    <div className="page">
      <header className="page-header overview-header">
        <div>
          <h1>选择一个工具开始</h1>
          <p>五个工具各自维护项目和历史记录。进入工具后即可上传、生成、审核和导出。</p>
        </div>
      </header>
      {error && <div className="notice error">{error}</div>}
      <div className="tool-card-grid">
        {cards.map((card) => {
          const local = status?.services[card.serviceKey];
          const recentProject = projects[card.id]?.[0];
          const recentJob = jobs.find((job) => job.tool_id === card.id);
          const textReady = status?.text_model.credential_state === "verified";
          const visionReady =
            status?.vision_model.credential_state === "configured";
          return (
            <article className="tool-card" key={card.id}>
              <div>
                <h2>{card.title}</h2>
                <p>{card.description}</p>
              </div>
              <ul className="plain-list">
                {card.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
              <div className="compact-status">
                <StatusLine
                  label="本地组件"
                  value={
                    local?.status === "available" || local?.status === "reachable"
                      ? "可用"
                      : local?.status === "unreachable"
                        ? "服务未启动"
                        : "未找到"
                  }
                  ok={local?.status === "available" || local?.status === "reachable"}
                />
                <StatusLine
                  label="文本模型"
                  value={
                    !card.needsText
                      ? "不需要"
                      : textReady
                        ? status?.text_model.model || "已验证"
                        : status?.text_model.message || "未验证"
                  }
                  ok={!card.needsText || textReady}
                  neutral={!card.needsText}
                />
                <StatusLine
                  label="视觉模型"
                  value={
                    !card.needsVision
                      ? "不需要"
                      : visionReady
                        ? status?.vision_model.model || "已配置"
                        : "未配置"
                  }
                  ok={!card.needsVision || visionReady}
                  neutral={!card.needsVision}
                />
              </div>
              <div className="recent-lines">
                <span>
                  最近项目：{recentProject?.display_name || "还没有项目"}
                </span>
                <span>
                  最近结果：
                  {recentJob
                    ? `${workflowLabels[recentJob.workflow_type] || "已生成数据"} · ${new Date(recentJob.completed_at || recentJob.created_at).toLocaleString("zh-CN")}`
                    : "还没有完成结果"}
                </span>
              </div>
              <Link className="button primary enter-button" href={card.href}>
                进入工具
              </Link>
            </article>
          );
        })}
      </div>
      <section className="help-strip">
        <strong>第一次使用？</strong>
        <span>先进入工具创建项目，再上传材料。模型或本地服务有问题时，到“设置”运行连接测试。</span>
        <Link href="/settings">检查设置</Link>
      </section>
    </div>
  );
}

function StatusLine({
  label,
  value,
  ok,
  neutral,
}: {
  label: string;
  value: string;
  ok: boolean;
  neutral?: boolean;
}) {
  return (
    <div>
      <span>{label}</span>
      <strong className={neutral ? "neutral-text" : ok ? "ok-text" : "warn-text"}>
        {value}
      </strong>
    </div>
  );
}
