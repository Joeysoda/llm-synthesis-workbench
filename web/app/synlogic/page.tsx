"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useEffect, useState } from "react";
import { api, getJob, type Job, type Sample } from "../../lib/api";
import { JobResult } from "../components/JobResult";
import { ProjectBar, useToolProject } from "../components/ProjectBar";

export default function SynLogicPage() {
  const workspace = useToolProject("synlogic");
  const [count, setCount] = useState(20);
  const [width, setWidth] = useState(5);
  const [height, setHeight] = useState(5);
  const [fillMin, setFillMin] = useState(0.3);
  const [fillMax, setFillMax] = useState(0.9);
  const [maxAttempts, setMaxAttempts] = useState(10000);
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api<{ services: { synlogic: { status: string } } }>(
      "/api/v2/integrations/status",
    )
      .then((value) => setReady(value.services.synlogic.status === "available"))
      .catch(() => setReady(false));
  }, []);

  useEffect(() => {
    if (!workspace.project) {
      setJobId("");
      return;
    }
    api<Job[]>(`/api/v2/jobs?project_id=${workspace.project.id}&limit=20`)
      .then((jobs) => {
        const latest = jobs.find((job) => job.workflow_type === "arrow-maze");
        setJobId(latest?.id || "");
      })
      .catch(() => setJobId(""));
  }, [workspace.project]);

  async function run() {
    if (!workspace.project) return;
    setBusy(true);
    setError("");
    try {
      const job = await api<Job>(
        `/api/v2/synlogic/projects/${workspace.project.id}/jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            workflow_type: "arrow-maze",
            num_of_data: count,
            width,
            height,
            arrow_fill_rate_min: fillMin,
            arrow_fill_rate_max: fillMax,
            max_attempts: maxAttempts,
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
        toolId="synlogic"
        title="SynLogic"
        subtitle="生成 Arrow Maze，并用上游 ArrowMazeVerifier 逐条检查答案。"
        connection={{
          label: ready ? "生成器与验证器可用" : "SynLogic 仓库未找到",
          tone: ready ? "ok" : "bad",
        }}
        workspace={workspace}
      />
      {workspace.project ? (
        <>
          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>生成 Arrow Maze</h2>
                <p>普通演示建议 20 条；压力验收可切换为 1,000 条。</p>
              </div>
              <div className="preset-row">
                <button className="button small" onClick={() => setCount(20)}>
                  20 条演示
                </button>
                <button className="button small" onClick={() => setCount(1000)}>
                  1,000 条验收
                </button>
              </div>
            </div>
            <div className="form-grid three">
              <label>
                生成数量
                <input type="number" min={1} max={1000} value={count} onChange={(e) => setCount(Number(e.target.value))} />
              </label>
              <label>
                宽度
                <input type="number" min={4} max={12} value={width} onChange={(e) => setWidth(Number(e.target.value))} />
              </label>
              <label>
                高度
                <input type="number" min={4} max={12} value={height} onChange={(e) => setHeight(Number(e.target.value))} />
              </label>
              <label>
                最小预填比例
                <input type="number" min={0} max={1} step={0.1} value={fillMin} onChange={(e) => setFillMin(Number(e.target.value))} />
              </label>
              <label>
                最大预填比例
                <input type="number" min={0} max={1} step={0.1} value={fillMax} onChange={(e) => setFillMax(Number(e.target.value))} />
              </label>
              <label>
                最大尝试次数
                <input type="number" min={100} max={100000} value={maxAttempts} onChange={(e) => setMaxAttempts(Number(e.target.value))} />
              </label>
            </div>
            <div className="run-row">
              <button
                className="button primary large"
                disabled={!ready || busy || fillMax < fillMin}
                onClick={() => void run()}
              >
                {busy ? "正在创建任务…" : "生成并验证"}
              </button>
              <span className={fillMax < fillMin ? "form-error" : "field-hint"}>
                {fillMax < fillMin
                  ? "最大预填比例不能小于最小值"
                  : "不调用外部模型，可直接本机运行"}
              </span>
            </div>
            {error && <div className="notice error">{error}</div>}
          </section>
          <JobResult
            jobId={jobId}
            renderSample={(sample) => (
              <MazeSample projectId={workspace.project!.id} sample={sample} />
            )}
          />
        </>
      ) : (
        <section className="empty-state">
          <h2>先建立一个 SynLogic 项目</h2>
          <p>迷宫参数、样本、验证记录和导出结果会保存在这个独立项目中。</p>
        </section>
      )}
    </div>
  );
}

function MazeSample({ projectId, sample }: { projectId: string; sample: Sample }) {
  const solution = parseGrid(sample.source.solution) || parseGrid(sample.answer) || [];
  const maze = parseGrid(sample.source.maze) || [];
  const [answer, setAnswer] = useState(JSON.stringify(solution, null, 2));
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setAnswer(JSON.stringify(solution, null, 2));
    setResult("");
    setError("");
    // sample.id uniquely identifies the selected puzzle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sample.id]);

  async function verify() {
    setBusy(true);
    setError("");
    setResult("");
    try {
      const parsed = JSON.parse(answer);
      if (
        !Array.isArray(parsed) ||
        !parsed.every(
          (row) => Array.isArray(row) && row.every((cell) => typeof cell === "string"),
        )
      ) {
        throw new Error("答案必须是字符串组成的二维 JSON 数组");
      }
      const job = await api<Job>(
        `/api/v2/synlogic/projects/${projectId}/verify`,
        {
          method: "POST",
          body: JSON.stringify({ sample_id: sample.id, answer: parsed }),
        },
      );
      let current = job;
      while (!["succeeded", "failed"].includes(current.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
        current = await getJob(job.id);
      }
      const data = (current.result?.data || {}) as { passed?: boolean };
      setResult(data.passed ? "验证通过：答案满足迷宫规则" : "验证拒绝：答案不满足迷宫规则");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "验证失败");
    } finally {
      setBusy(false);
    }
  }

  function damage() {
    const next = solution.map((row) => [...row]);
    if (next[0]?.length) next[0][0] = next[0][0] === "↑" ? "↓" : "↑";
    setAnswer(JSON.stringify(next, null, 2));
    setResult("已人为修改第一个单元格，请重新验证。");
  }

  return (
    <article className="maze-sample">
      <div className="maze-panels">
        <div>
          <h3>题目网格</h3>
          <Grid value={maze} empty="·" />
        </div>
        <div>
          <h3>标准答案</h3>
          <Grid value={solution} empty="·" />
        </div>
      </div>
      <div className="answer-editor">
        <label>
          待验证的二维答案
          <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={9} />
        </label>
        <div className="run-row">
          <button className="button primary" disabled={busy} onClick={() => void verify()}>
            {busy ? "验证中…" : "调用原始验证器"}
          </button>
          <button className="button" onClick={damage}>人为破坏一格</button>
        </div>
        {result && <div className={result.startsWith("验证通过") ? "notice success" : "notice"}>{result}</div>}
        {error && <div className="notice error">{error}</div>}
      </div>
    </article>
  );
}

function parseGrid(value: unknown): string[][] | null {
  if (Array.isArray(value) && value.every(Array.isArray)) return value as string[][];
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) && parsed.every(Array.isArray)
        ? (parsed as string[][])
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

function Grid({ value, empty }: { value: string[][]; empty: string }) {
  if (!value.length) return <div className="empty-compact">没有可显示的网格</div>;
  return (
    <div
      className="maze-grid"
      style={{ gridTemplateColumns: `repeat(${value[0].length}, minmax(34px, 1fr))` }}
    >
      {value.flatMap((row, rowIndex) =>
        row.map((cell, columnIndex) => (
          <span key={`${rowIndex}-${columnIndex}`}>{cell || empty}</span>
        )),
      )}
    </div>
  );
}
