export const API_BASE =
  process.env.NEXT_PUBLIC_GATEWAY_BASE || "http://127.0.0.1:18000";

export type ToolId =
  | "synthetic-data-kit"
  | "easy-dataset"
  | "synlogic"
  | "kaqg"
  | "cleanlab";

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
