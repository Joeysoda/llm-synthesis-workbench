export const API_BASE =
  process.env.NEXT_PUBLIC_GATEWAY_BASE || "http://127.0.0.1:18000";

export type ToolId =
  | "synthetic-data-kit"
  | "easy-dataset"
  | "synlogic"
  | "kaqg"
  | "cleanlab"
  | "medical";

export type Project = {
  id: string;
  tool_id: ToolId | "legacy";
  display_name: string;
  description: string;
  archived_at?: string | null;
  asset_count: number;
  run_count: number;
  latest_run_id?: string | null;
};

export type Asset = {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
};

export type Stage = {
  id: string;
  label: string;
  status: "pending" | "running" | "succeeded" | "failed";
};

export type Job = {
  id: string;
  project_id: string;
  project_name?: string;
  tool_id?: ToolId | "legacy";
  tool_name: string;
  workflow_type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  current_stage: string;
  stages: Stage[];
  progress: { completed?: number; total?: number };
  input: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  artifact_path?: string | null;
  created_at: string;
  completed_at?: string | null;
};

export type Sample = {
  id: string;
  task_type: string;
  question: string;
  answer: string;
  reasoning: string;
  source: Record<string, unknown>;
  generation: Record<string, unknown>;
  quality: {
    rule_passed?: boolean;
    model_score?: number;
    human_status?: string;
    target_difficulty?: string;
    difficulty_code?: number;
    feature_scores?: Record<string, number>;
    decision?: string;
    original_label?: string;
    suggested_label?: string;
    current_label?: string;
    label_score?: number | null;
    is_label_issue?: boolean;
    outlier_score?: number | null;
    is_outlier_issue?: boolean;
    near_duplicate_score?: number | null;
    is_near_duplicate_issue?: boolean;
    near_duplicate_sets?: unknown[];
  };
};

export type PipelineNode = { id: string; type: string; label: string; config: Record<string, unknown> };
export type PipelineEdge = { source: string; target: string };
export type Pipeline = {
  id: string; name: string; description: string; domain: "general" | "medical";
  project_id?: string | null; nodes: PipelineNode[]; edges: PipelineEdge[]; version: number;
  created_at: string; updated_at: string;
};
export type PipelineRun = {
  id: string; pipeline_id: string; project_id?: string | null;
  status: "queued" | "running" | "succeeded" | "needs_review" | "failed" | "cancelled";
  current_node: string; nodes: (PipelineNode & { status: string })[];
  error?: string | null; result?: Record<string, unknown> | null; artifact_path?: string | null;
  created_at: string; completed_at?: string | null;
};
export type Dataset = {
  id: string; name: string; domain: string; project_id?: string | null; created_at: string;
  version_id?: string | null; version?: string | null; status?: string | null; quality?: { passed?: boolean; rules?: Record<string, unknown> };
  project_name?: string | null; manifest?: Record<string, unknown>; artifact_path?: string | null;
};
export type DatasetVersion = {
  id: string; dataset_id: string; version: string; status: string; dataset_name: string; domain: string;
  manifest: Record<string, unknown>; quality: { passed?: boolean; rules?: Record<string, unknown> };
  artifact_path?: string | null; created_at: string; published_at?: string | null;
};

export type MedicalOutput = {
  id: string; label: string; category: "csv" | "fhir" | "derived" | "report";
  description: string; required_min: "omit" | "internal" | "publish";
  depends_on: string[]; modes: ("omit" | "internal" | "publish")[];
};

export type MedicalProject = Project & {
  dataset_count: number; latest_dataset?: Dataset | null; quality_passed?: boolean | null;
  resource_count: number; timeline_count: number; task_count: number;
  latest_pipeline_run_id?: string | null; latest_pipeline_run_status?: string | null;
};

export type MedicalArtifact = {
  id: string; label: string; description: string; category: string; mode: string;
  relative_path: string; format: string; size: number; row_count?: number; columns?: string[];
};

export type MedicalProjectAssets = { project: Project; versions: (DatasetVersion & { artifacts: MedicalArtifact[] })[] };

async function readError(response: Response) {
  try {
    const data = await response.json();
    return data.detail || data.message || data.error || response.statusText;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

export async function api<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json() as Promise<T>;
}

export function listProjects(toolId: ToolId) {
  return api<Project[]>(`/api/v2/tools/${toolId}/projects`);
}

export function createProject(toolId: ToolId, name: string) {
  return api<Project>(`/api/v2/tools/${toolId}/projects`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function patchProject(
  projectId: string,
  patch: { name?: string; archived?: boolean },
) {
  return api<Project>(`/api/v2/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function listAssets(projectId: string) {
  return api<Asset[]>(`/api/v2/projects/${projectId}/assets`);
}

export async function uploadAsset(projectId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return api<Asset>(`/api/v2/projects/${projectId}/assets`, {
    method: "POST",
    body: form,
  });
}

export function getJob(jobId: string) {
  return api<Job>(`/api/v2/jobs/${jobId}`);
}

export function getSamples(jobId: string) {
  return api<Sample[]>(`/api/v2/jobs/${jobId}/samples`);
}

export function patchSample(
  sampleId: string,
  patch: {
    question?: string;
    answer?: string;
    reasoning?: string;
    quality?: Record<string, unknown>;
  },
) {
  return api<Sample>(`/api/v1/samples/${encodeURIComponent(sampleId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function exportUrl(jobId: string) {
  return `${API_BASE}/api/v2/jobs/${jobId}/export`;
}

export function artifactUrl(jobId: string, artifactName: string) {
  return `${API_BASE}/api/v2/jobs/${jobId}/artifacts/${artifactName}`;
}

export function listPipelineTemplates() { return api<Omit<Pipeline, "version" | "created_at" | "updated_at">[]>("/api/v3/pipelines/templates"); }
export function listPipelines() { return api<Pipeline[]>("/api/v3/pipelines"); }
export function createPipeline(payload: Omit<Pipeline, "id" | "version" | "created_at" | "updated_at">) {
  return api<Pipeline>("/api/v3/pipelines", { method: "POST", body: JSON.stringify(payload) });
}
export function validatePipeline(pipelineId: string) { return api<{ valid: boolean; errors: { code: string; message: string }[] }>(`/api/v3/pipelines/${pipelineId}/validate`, { method: "POST" }); }
export function startPipeline(pipelineId: string, projectId?: string, parameters: Record<string, unknown> = {}) {
  return api<PipelineRun>(`/api/v3/pipelines/${pipelineId}/runs`, { method: "POST", body: JSON.stringify({ project_id: projectId, parameters, confirmed: true }) });
}
export function getPipelineRun(runId: string) { return api<PipelineRun>(`/api/v3/pipeline-runs/${runId}`); }
export function createMedicalRun(projectId: string, payload: Record<string, unknown>) {
  return api<PipelineRun>(`/api/v3/medical/projects/${projectId}/generate`, { method: "POST", body: JSON.stringify({ ...payload, confirmed: true }) });
}
export function listMedicalOutputCatalog() { return api<MedicalOutput[]>("/api/v3/medical/output-catalog"); }
export function createMedicalGeneration(payload: Record<string, unknown>) {
  return api<PipelineRun & { project: Project; resolved_output_policy: Record<string, string>; dependency_reasons: Record<string, string[]> }>("/api/v3/medical/generations", { method: "POST", body: JSON.stringify({ ...payload, confirmed: true }) });
}
export function listMedicalProjects() { return api<MedicalProject[]>("/api/v3/medical/projects"); }
export function getMedicalProjectAssets(projectId: string) { return api<MedicalProjectAssets>(`/api/v3/medical/projects/${projectId}/assets`); }
export function medicalArtifactPreview(versionId: string, artifactId: string, offset = 0, limit = 50) {
  return api<{ artifact: MedicalArtifact; offset: number; limit: number; total: number; columns?: string[]; rows?: unknown[]; content?: string }>(`/api/v3/dataset-versions/${versionId}/artifacts/${encodeURIComponent(artifactId)}/preview?offset=${offset}&limit=${limit}`);
}
export function medicalArtifactDownload(versionId: string, artifactId: string) { return `${API_BASE}/api/v3/dataset-versions/${versionId}/artifacts/${encodeURIComponent(artifactId)}/download`; }
export function listDatasets() { return api<Dataset[]>("/api/v3/datasets"); }
export function listDatasetVersions(datasetId: string) { return api<DatasetVersion[]>(`/api/v3/datasets/${datasetId}/versions`); }
export function publishDataset(datasetId: string, versionId: string) { return api<DatasetVersion>(`/api/v3/datasets/${datasetId}/versions/${versionId}/publish`, { method: "POST" }); }
