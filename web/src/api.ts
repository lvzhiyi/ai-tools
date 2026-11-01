// 后端接口的类型和请求函数。字段和 studio/web/projects.py、jobs.py 返回的结构一一对应。

export type Status = "ok" | "warn" | "fail";
export interface Check { name: string; status: Status; detail: string }

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export interface Job {
  id: string; kind: string; title: string; argv: string[]; command: string;
  project: string | null; status: JobStatus;
  created: number; started: number | null; finished: number | null;
  returncode: number | null; progress: [number, number] | null; stage: string | null;
  line_count: number; lines?: string[];
}

export interface StoryboardItem {
  file: string; title: string; error: string | null; shots: number; characters: number; updated: number;
  done: { images: number; audio: number; lines: number; clips: number; preview: boolean };
  cover: string | null;
}

export interface Character { name?: string; look: string; ref?: string }
export interface Shot {
  id: number | string; image: string; motion?: string; line?: string; voice?: string;
  emotion?: string; seed?: number; duration?: number; characters?: string[];
}
export interface Storyboard {
  title: string; defaults?: Record<string, string>;
  characters: Record<string, Character>; shots: Shot[];
}

export interface ShotQA {
  passed: boolean; attempts: number; mode: string;
  face: Record<string, number | null>; face_ref: Record<string, string>;
  match_score: number | null; summary: string | null; defects: string[];
  extreme_expression: boolean | null; notes: string[]; gen_sec: number | null;
}
export interface ClipQA {
  sec: number; frames: number; video_sec: number; peak_gb: number | null; seed?: number;
  review?: boolean; prompt?: string; time?: string;
  face?: { frames: number; skipped: number; identity_min?: number; identity_median?: number; drift_min?: number | null };
}
export interface ShotView {
  name: string; id: number | string; image_prompt: string; motion: string; line: string;
  voice: string | null; emotion: string | null; characters: string[];
  image: string | null; candidates: { name: string; url: string }[];
  clip: string | null; audio: string | null;
  timeline: { start: number; duration: number; speech: number } | null;
  qa: ShotQA | null; i2v: ClipQA | null;
}
export interface RunRow {
  id: string; status: string; command: string; started: string; git: string;
  total_sec: number; stage_sec: Record<string, number>;
}
export interface Project {
  file: string; title: string; dir: string; shots: ShotView[];
  cast: { id: string; name: string; look: string; images: { label: string; url: string }[] }[];
  preview: string | null; subtitles: string | null; qa_report: string | null; runs: RunRow[];
}
export interface HistoryRow { [k: string]: string }
export interface Overview {
  checks: Check[]; jobs: { current: Job | null; queued: number }; recent_jobs: Job[];
  history: HistoryRow[]; storyboards: number;
}
export interface LabelPair { key: string; a: string; b: string; a_url: string; b_url: string; character: string }
export interface Calibration {
  profile: string; report: string; svg: string | null; updated: number;
  summary: { verdict?: string; suggested?: number; separable?: boolean;
    compare?: { threshold: number; false_reject_rate: number; false_accept_rate: number; agreement?: number }[] };
}

export class ApiError extends Error {}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError((data && (data as { error?: string }).error) || `${r.status} ${r.statusText}`);
  return data as T;
}

const enc = encodeURIComponent;
export const api = {
  overview: () => req<Overview>("GET", "/api/overview"),
  checks: () => req<Check[]>("GET", "/api/checks"),
  config: () => req<Record<string, unknown>>("GET", "/api/config"),
  history: (project?: string) => req<HistoryRow[]>("GET", "/api/history" + (project ? `?project=${enc(project)}` : "")),
  storyboards: () => req<StoryboardItem[]>("GET", "/api/storyboards"),
  newStoryboard: (file: string, title: string) => req<{ file: string }>("POST", "/api/storyboards", { file, title }),
  storyboard: (file: string) => req<{ file: string; data: Storyboard }>("GET", `/api/storyboards/${enc(file)}`),
  saveStoryboard: (file: string, data: Storyboard) =>
    req<{ file: string; data: Storyboard }>("PUT", `/api/storyboards/${enc(file)}`, { data }),
  project: (file: string) => req<Project>("GET", `/api/projects/${enc(file)}`),
  runReport: (file: string, run: string) => req<string>("GET", `/api/projects/${enc(file)}/runs/${enc(run)}`),
  redo: (file: string, kind: "image" | "audio" | "clip", shot: string) =>
    req<{ job: Job; archived: string | null }>("POST", `/api/projects/${enc(file)}/redo`, { kind, shot }),
  labels: (file: string) =>
    req<{ pairs: LabelPair[]; labels: Record<string, string>; stale: number }>("GET", `/api/projects/${enc(file)}/label`),
  label: (file: string, key: string, value: string) => req("POST", `/api/projects/${enc(file)}/label`, { key, value }),
  scripts: () => req<{ file: string; chars: number; updated: number; storyboard: boolean }[]>("GET", "/api/scripts"),
  script: (file: string) => req<string>("GET", `/api/scripts/${enc(file)}`),
  saveScript: (file: string, text: string) => req<{ file: string }>("POST", "/api/scripts", { file, text }),
  calibrations: () => req<Calibration[]>("GET", "/api/calibrations"),
  jobs: () => req<Job[]>("GET", "/api/jobs"),
  job: (id: string) => req<Job>("GET", `/api/jobs/${enc(id)}`),
  submit: (kind: string, file: string | null, options: Record<string, unknown> = {}) =>
    req<Job>("POST", "/api/jobs", { kind, file, options }),
  cancel: (id: string) => req<Job>("POST", `/api/jobs/${enc(id)}/cancel`),
};

export const fmtSec = (s: number | string | null | undefined): string => {
  if (s === null || s === undefined || s === "") return "-";
  const n = Number(s);
  if (n >= 90) return `${(n / 60).toFixed(1)} 分钟`;
  return `${n.toFixed(n < 10 ? 1 : 0)} 秒`;
};

export const fmtTime = (t: number | null | undefined): string =>
  t ? new Date(t * 1000).toLocaleString("zh-CN", { hour12: false }) : "-";
