import { marked } from "marked";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Job, JobStatus, Status } from "../api";
import { fmtSec } from "../api";

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="error-box">
      <b>出错了：</b>{error}
      {onRetry ? <button className="btn small" onClick={onRetry}>重试</button> : null}
    </div>
  );
}

export const Loading = ({ text = "加载中…" }: { text?: string }) => <div className="muted pad">{text}</div>;

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-title">{title}</div>{children}</div>;
}

const STATUS_TEXT: Record<Status, string> = { ok: "正常", warn: "可选", fail: "有问题" };
const STATUS_ICON: Record<Status, string> = { ok: "✓", warn: "–", fail: "✕" };
export const CheckBadge = ({ status }: { status: Status }) =>
  <span className={`badge ${status}`}>{STATUS_ICON[status]} {STATUS_TEXT[status]}</span>;

const JOB_TEXT: Record<JobStatus, string> = {
  queued: "排队中", running: "运行中", succeeded: "完成", failed: "失败", cancelled: "已取消",
};
export const JobBadge = ({ status }: { status: JobStatus }) => <span className={`badge job-${status}`}>{JOB_TEXT[status]}</span>;

export function jobElapsed(j: Job): string {
  if (!j.started) return "";
  return fmtSec(((j.finished ?? Date.now() / 1000) - j.started));
}

export function Progress({ job }: { job: Job }) {
  const p = job.progress;
  const frac = p && p[1] ? p[0] / p[1] : null;
  return (
    <div className="progress" title={p ? `${p[0]} / ${p[1]}` : "进行中"}>
      <div className={`progress-fill ${frac === null ? "indeterminate" : ""}`} style={{ width: frac === null ? "30%" : `${frac * 100}%` }} />
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  // 报告里的相对图片（比如校准报告的 distribution.svg）由调用方替换成可访问的地址
  const html = useMemo(() => marked.parse(text, { async: false }) as string, [text]);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** 日志面板：新行到来时自动滚到底；用户往上翻时暂停自动滚动。 */
export function LogView({ lines, height = 360 }: { lines: string[]; height?: number }) {
  const ref = useRef<HTMLPreElement>(null);
  const [follow, setFollow] = useState(true);
  useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines, follow]);
  return (
    <div className="log-wrap">
      <pre ref={ref} className="log" style={{ height }}
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 30);
        }}>
        {lines.map((l, i) => <div key={i} className={lineClass(l)}>{l}</div>)}
      </pre>
      {!follow ? <button className="btn small follow" onClick={() => setFollow(true)}>↓ 回到最新</button> : null}
    </div>
  );
}

function lineClass(l: string): string {
  if (/❌|Error|Traceback|失败/.test(l)) return "l-err";
  if (/⚠️|👀|WARN/.test(l)) return "l-warn";
  if (/✅|完成/.test(l)) return "l-ok";
  if (l.startsWith("$ ")) return "l-cmd";
  return "";
}

export function Tabs({ tabs, active, onChange }: { tabs: [string, string][]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={active === k} className={`tab ${active === k ? "on" : ""}`} onClick={() => onChange(k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** 人脸相似度：低于阈值标红 + 👀，图标和文字一起，不只靠颜色。 */
export function FaceScore({ value, threshold = 0.4, label }: { value: number | null | undefined; threshold?: number; label?: string }) {
  if (value === null || value === undefined) return <span className="muted">{label ? `${label} ` : ""}没检测到脸</span>;
  const low = value < threshold;
  return <span className={low ? "score low" : "score"}>{label ? `${label} ` : ""}{value.toFixed(2)}{low ? " 👀" : ""}</span>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}{hint ? <span className="hint">{hint}</span> : null}</span>
      {children}
    </label>
  );
}
