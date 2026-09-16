"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getMedicalProjectAssets, listMedicalProjects, medicalArtifactDownload, medicalArtifactPreview, type MedicalArtifact, type MedicalProject, type MedicalProjectAssets } from "../../lib/api";

function formatSize(size: number) { if (size < 1024) return `${size} B`; if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`; return `${(size / 1024 / 1024).toFixed(1)} MB`; }
function statusText(project: MedicalProject) { if (project.quality_passed === true) return "质量通过"; if (project.quality_passed === false) return "需要检查"; return "尚未生成"; }

export default function DatasetsPage() {
  const searchParams = useSearchParams();
  const [projects, setProjects] = useState<MedicalProject[]>([]); const [selectedId, setSelectedId] = useState(""); const [assets, setAssets] = useState<MedicalProjectAssets | null>(null); const [selectedVersion, setSelectedVersion] = useState(""); const [selectedArtifact, setSelectedArtifact] = useState<MedicalArtifact | null>(null); const [preview, setPreview] = useState<{ columns?: string[]; rows?: unknown[]; content?: string; total?: number } | null>(null); const [message, setMessage] = useState(""); const [loadingProjects, setLoadingProjects] = useState(true); const [reloadToken, setReloadToken] = useState(0);
  useEffect(() => {
    let disposed = false;
    async function loadProjects() {
      setLoadingProjects(true);
      try {
        const items = await listMedicalProjects();
        if (disposed) return;
        setProjects(items);
        const requested = searchParams.get("project");
        setSelectedId(requested && items.some((item) => item.id === requested) ? requested : items[0]?.id || "");
        setMessage("");
      } catch (error) {
        if (!disposed) setMessage(error instanceof Error ? error.message : "读取医疗项目失败");
      } finally {
        if (!disposed) setLoadingProjects(false);
      }
    }
    void loadProjects();
    const refreshOnFocus = () => void loadProjects();
    window.addEventListener("focus", refreshOnFocus);
    return () => { disposed = true; window.removeEventListener("focus", refreshOnFocus); };
  }, [searchParams, reloadToken]);
  useEffect(() => { if (!selectedId) return; void getMedicalProjectAssets(selectedId).then((next) => { setAssets(next); setSelectedVersion(next.versions[0]?.id || ""); setSelectedArtifact(next.versions[0]?.artifacts[0] || null); }).catch((error: Error) => setMessage(error.message)); }, [selectedId]);
  useEffect(() => { if (!selectedVersion || !selectedArtifact) { setPreview(null); return; } void medicalArtifactPreview(selectedVersion, selectedArtifact.id).then(setPreview).catch((error: Error) => setMessage(error.message)); }, [selectedVersion, selectedArtifact]);
  const version = assets?.versions.find((item) => item.id === selectedVersion);
  const currentProject = projects.find((item) => item.id === selectedId);
  return <div className="page wide-page"><header className="page-header"><div><h1>医疗数据资产</h1><p>先选择项目，再查看该项目生成的表格、FHIR 资源、患者时间线、任务样本和质量报告。每个项目的数据都不会与其他项目混在一起。</p></div><Link className="button primary" href="/medical">创建医疗数据</Link></header>{message && <div className="notice error">{message}</div>}
    <div className="asset-layout medical-assets"><section className="panel asset-list"><h2>医疗项目</h2>{loadingProjects ? <p className="muted">正在读取医疗项目…</p> : projects.length ? projects.map((item) => <button className={selectedId === item.id ? "asset-item active" : "asset-item"} key={item.id} onClick={() => setSelectedId(item.id)}><strong>{item.display_name}</strong><span>{new Date(item.created_at).toLocaleString("zh-CN")}</span><span>{statusText(item)} · {item.resource_count || 0} 个 FHIR 资源 · {item.task_count || 0} 条任务</span></button>) : <><p className="muted">还没有医疗项目。完成一次医疗数据生成后，项目会自动出现在这里。</p><button className="button" onClick={() => setReloadToken((value) => value + 1)}>重新读取</button></>}</section><section className="panel asset-detail">{currentProject && <><div className="asset-detail-heading"><div><h2>{currentProject.display_name}</h2><p className="muted">项目 ID：{currentProject.id}</p></div><span className={currentProject.quality_passed ? "ok-text" : "warn-text"}>{statusText(currentProject)}</span></div>{assets?.versions.length ? <><label className="version-select">数据集版本<select value={selectedVersion} onChange={(event) => { setSelectedVersion(event.target.value); const next = assets.versions.find((item) => item.id === event.target.value); setSelectedArtifact(next?.artifacts[0] || null); }}>{assets.versions.map((item) => <option key={item.id} value={item.id}>{item.version} · {item.status}</option>)}</select></label><div className="artifact-grid">{version?.artifacts.map((artifact) => <button className={selectedArtifact?.id === artifact.id ? "artifact-card active" : "artifact-card"} key={artifact.id} onClick={() => setSelectedArtifact(artifact)}><strong>{artifact.label}</strong><span>{artifact.description}</span><small>{artifact.format.toUpperCase()} · {artifact.row_count ?? 1} 行 · {formatSize(artifact.size)}</small></button>)}</div>{selectedArtifact && <section className="preview-panel"><div className="preview-heading"><div><h3>{selectedArtifact.label}</h3><p>{selectedArtifact.description} · 共 {preview?.total ?? selectedArtifact.row_count ?? 0} 行</p></div><a className="button" href={medicalArtifactDownload(selectedVersion, selectedArtifact.id)}>下载</a></div>{preview?.content ? <pre className="result-pre">{preview.content}</pre> : preview?.rows?.length ? preview.columns ? <div className="table-scroll"><table><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.rows.map((row, index) => <tr key={index}>{preview.columns?.map((column) => <td key={column}>{String((row as Record<string, unknown>)[column] ?? "")}</td>)}</tr>)}</tbody></table></div> : <div className="json-preview">{preview.rows.map((row, index) => <pre className="result-pre" key={index}>{JSON.stringify(row, null, 2)}</pre>)}</div> : <p className="muted">该产物暂无可预览内容。</p>}</section>}</> : <p className="muted">该项目还没有完成数据集版本。</p>}</>}</section></div>
  </div>;
}
