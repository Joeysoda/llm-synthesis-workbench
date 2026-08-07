"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Status = {
  text_model: {
    base_url: string;
    model: string;
    judge_model: string;
    credential_state: string;
    message: string;
  };
  vision_model: {
    base_url: string;
    model: string;
    credential_state: string;
    message: string;
  };
  services: Record<
    string,
    { status: string; endpoint?: string; path?: string; version?: string; message?: string }
  >;
};

export default function SettingsPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [probing, setProbing] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      setStatus(await api<Status>("/api/v2/integrations/status"));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置读取失败");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function probe(profile: string) {
    setProbing(profile);
    try {
      const result = await api<{ ok: boolean; message: string }>(
        `/api/v2/integrations/${profile}/probe`,
        { method: "POST", body: JSON.stringify({}) },
      );
      setResults((current) => ({ ...current, [profile]: result }));
      await load();
    } catch (reason) {
      setResults((current) => ({
        ...current,
        [profile]: {
          ok: false,
          message: reason instanceof Error ? reason.message : "测试失败",
        },
      }));
    } finally {
      setProbing("");
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p>这里只显示本机环境状态，不接收也不展示任何密钥明文。</p>
        </div>
      </header>
      {error && <div className="notice error">{error}</div>}
      <section className="settings-section">
        <div className="settings-heading">
          <div>
            <h2>文本模型</h2>
            <p>用于 Synthetic Data Kit、Easy Dataset 和 KAQG 的文本生成与质量评估。</p>
          </div>
          <StateTag state={status?.text_model.credential_state || "unknown"} />
        </div>
        <dl className="settings-grid">
          <dt>接口地址</dt><dd>{status?.text_model.base_url || "—"}</dd>
          <dt>生成模型</dt><dd>{status?.text_model.model || "—"}</dd>
          <dt>Judge 模型</dt><dd>{status?.text_model.judge_model || "—"}</dd>
          <dt>密钥状态</dt><dd>{status?.text_model.message || "正在读取"}</dd>
        </dl>
        <ProbeRow
          profile="text"
          busy={probing === "text"}
          result={results.text}
          onProbe={probe}
        />
      </section>
      <section className="settings-section">
        <div className="settings-heading">
          <div>
            <h2>视觉模型</h2>
            <p>专用于图片问答；DeepSeek 文本模型不会被当作视觉模型使用。</p>
          </div>
          <StateTag state={status?.vision_model.credential_state || "unknown"} />
        </div>
        <dl className="settings-grid">
          <dt>接口地址</dt><dd>{status?.vision_model.base_url || "未配置"}</dd>
          <dt>模型</dt><dd>{status?.vision_model.model || "未配置"}</dd>
          <dt>密钥状态</dt><dd>{status?.vision_model.message || "正在读取"}</dd>
        </dl>
        <ProbeRow
          profile="vision"
          busy={probing === "vision"}
          result={results.vision}
          onProbe={probe}
        />
      </section>
      <section className="settings-section">
        <div className="settings-heading">
          <div>
            <h2>本地服务</h2>
            <p>展示网关、sidecar、CLI 和上游仓库的实际地址与版本。</p>
          </div>
        </div>
        <div className="service-table">
          <ServiceRow label="Gateway" item={status?.services.gateway} />
          <ServiceRow label="Easy Dataset sidecar" item={status?.services.easy_dataset} />
          <ServiceRow label="Synthetic CLI" item={status?.services.synthetic_cli} />
          <ServiceRow label="SynLogic 仓库" item={status?.services.synlogic} />
          <ServiceRow label="KAQG worker" item={status?.services.kaqg_worker} />
          <ServiceRow label="Cleanlab" item={status?.services.cleanlab} />
        </div>
        <ProbeRow
          profile="easy-dataset"
          label="测试 Easy Dataset"
          busy={probing === "easy-dataset"}
          result={results["easy-dataset"]}
          onProbe={probe}
        />
        <ProbeRow
          profile="kaqg"
          label="测试 KAQG"
          busy={probing === "kaqg"}
          result={results.kaqg}
          onProbe={probe}
        />
        <ProbeRow
          profile="cleanlab"
          label="测试 Cleanlab"
          busy={probing === "cleanlab"}
          result={results.cleanlab}
          onProbe={probe}
        />
      </section>
      <section className="settings-note">
        <strong>如何注入新密钥</strong>
        <p>
          在启动网关的终端环境中设置新的 DEEPSEEK_API_KEY 和
          LLM_CREDENTIAL_ROTATED=true，然后重启本机服务。密钥不会写入数据库、日志或网页。
        </p>
      </section>
    </div>
  );
}

function StateTag({ state }: { state: string }) {
  const labels: Record<string, string> = {
    verified: "已验证",
    "ready-to-probe": "待测试",
    "rotation-unconfirmed": "未确认轮换",
    "not-configured": "未配置",
    configured: "已配置",
    unknown: "读取中",
  };
  const good = ["verified", "configured"].includes(state);
  return <span className={`status-badge ${good ? "succeeded" : "failed"}`}>{labels[state] || state}</span>;
}

function ProbeRow({
  profile,
  label = "测试连接",
  busy,
  result,
  onProbe,
}: {
  profile: string;
  label?: string;
  busy: boolean;
  result?: { ok: boolean; message: string };
  onProbe: (profile: string) => Promise<void>;
}) {
  return (
    <div className="probe-row">
      <button className="button" disabled={busy} onClick={() => void onProbe(profile)}>
        {busy ? "正在测试…" : label}
      </button>
      {result && <span className={result.ok ? "ok-text" : "warn-text"}>{result.message}</span>}
    </div>
  );
}

function ServiceRow({
  label,
  item,
}: {
  label: string;
  item?: { status: string; endpoint?: string; path?: string; version?: string; message?: string };
}) {
  const ready = item?.status === "reachable" || item?.status === "available";
  return (
    <div>
      <strong>{label}</strong>
      <span>{item?.endpoint || item?.path || "—"}</span>
      <code>{item?.version || "—"}</code>
      <em className={ready ? "ok-text" : "warn-text"}>
        {ready ? "可用" : item?.status || "读取中"}
      </em>
    </div>
  );
}
