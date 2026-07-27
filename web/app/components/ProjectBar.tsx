"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { useCallback, useEffect, useState } from "react";
import {
  createProject,
  listProjects,
  patchProject,
  type Project,
  type ToolId,
} from "../../lib/api";

export function useToolProject(toolId: ToolId) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const storageKey = `workbench:last-project:${toolId}`;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listProjects(toolId);
      setProjects(rows);
      const saved =
        typeof window !== "undefined" ? localStorage.getItem(storageKey) : "";
      const selected = rows.find((item) => item.id === saved) || rows[0] || null;
      setProject(selected);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目读取失败");
    } finally {
      setLoading(false);
    }
  }, [storageKey, toolId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function choose(next: Project | null) {
    setProject(next);
    if (next) localStorage.setItem(storageKey, next.id);
  }

  return { projects, project, choose, refresh, loading, error };
}

export function ProjectBar({
  toolId,
  title,
  subtitle,
  connection,
  workspace,
}: {
  toolId: ToolId;
  title: string;
  subtitle: string;
  connection: { label: string; tone: "ok" | "warn" | "bad" | "neutral" };
  workspace: ReturnType<typeof useToolProject>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");

  async function submit() {
    if (!name.trim()) return;
    try {
      const created = await createProject(toolId, name.trim());
      await workspace.refresh();
      workspace.choose(created);
      setName("");
      setCreating(false);
      setMessage("");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "创建失败");
    }
  }

  async function rename() {
    if (!workspace.project) return;
    const next = window.prompt("新的项目名称", workspace.project.display_name);
    if (!next?.trim() || next.trim() === workspace.project.display_name) return;
    try {
      await patchProject(workspace.project.id, { name: next.trim() });
      await workspace.refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "重命名失败");
    }
  }

  async function archive() {
    if (!workspace.project) return;
    if (!window.confirm(`归档“${workspace.project.display_name}”？历史任务和产物仍会保留。`)) return;
    await patchProject(workspace.project.id, { archived: true });
    await workspace.refresh();
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        <span className={`connection ${connection.tone}`}>
          <span className="status-dot" />
          {connection.label}
        </span>
      </header>
      <section className="project-bar" aria-label={`${title} 项目`}>
        <label>
          当前项目
          <select
            value={workspace.project?.id || ""}
            onChange={(event) =>
              workspace.choose(
                workspace.projects.find((item) => item.id === event.target.value) || null,
              )
            }
          >
            {!workspace.projects.length && <option value="">尚未创建项目</option>}
            {workspace.projects.map((item) => (
              <option value={item.id} key={item.id}>
                {item.display_name}
              </option>
            ))}
          </select>
        </label>
        <button className="button primary" onClick={() => setCreating(true)}>
          新建项目
        </button>
        <button className="button" disabled={!workspace.project} onClick={rename}>
          重命名
        </button>
        <button className="button quiet" disabled={!workspace.project} onClick={archive}>
          归档
        </button>
      </section>
      {creating && (
        <section className="inline-form">
          <label>
            项目名称
            <input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
              placeholder="例如：产品文档问答"
            />
          </label>
          <button className="button primary" onClick={() => void submit()}>
            创建
          </button>
          <button className="button" onClick={() => setCreating(false)}>
            取消
          </button>
          {message && <span className="form-error">{message}</span>}
        </section>
      )}
      {workspace.error && <div className="notice error">{workspace.error}</div>}
    </>
  );
}
