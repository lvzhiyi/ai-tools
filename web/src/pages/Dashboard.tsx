import { useState } from "react";
import { api, fmtSec, fmtTime } from "../api";
import { href, useAsync, useOnProjectJobFinished } from "../hooks";
import { CheckBadge, ErrorBox, JobBadge, Loading, jobElapsed } from "../components/ui";
import { StackedBars } from "../components/StackedBars";

export function Dashboard() {
  const { data, error, reload } = useAsync(() => api.overview(), []);
  const [checking, setChecking] = useState(false);
  useOnProjectJobFinished(null, reload);
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Loading text="检查环境中（ComfyUI、LM Studio……）" />;

  const recheck = async () => {
    setChecking(true);
    try { await api.checks(); reload(); } finally { setChecking(false); }
  };
  const fails = data.checks.filter((c) => c.status === "fail").length;
  const bars = data.history.slice().reverse().map((r) => ({
    id: r.time + r.command, label: r.time.slice(5, 16), sub: `${r.project} · ${r.command.replace(/^studio /, "")}`,
    status: r.status,
    values: { images: +r.images_sec || 0, audio: +r.audio_sec || 0, i2v: +r.i2v_sec || 0, subtitle: +r.subtitle_sec || 0, video: +r.video_sec || 0 },
  }));

  return (
    <div className="stack">
      <div className="page-head">
        <h1>总览</h1>
        <div className="row">
          <a className="btn primary" href={href("projects")}>打开项目</a>
          <a className="btn" href={href("scripts")}>从剧本开始</a>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>环境 {fails ? <span className="badge fail">{fails} 项有问题</span> : <span className="badge ok">✓ 必需组件都正常</span>}</h2>
          <button className="btn small" onClick={recheck} disabled={checking}>{checking ? "检测中…" : "重新检测"}</button>
        </div>
        <div className="checks">
          {data.checks.map((c) => (
            <div key={c.name} className={`check ${c.status}`}>
              <div className="check-name">{c.name}<CheckBadge status={c.status} /></div>
              <div className="check-detail">{c.detail}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid2">
        <section className="card">
          <div className="card-head"><h2>最近的任务</h2><a href={href("jobs")} className="small">全部 →</a></div>
          {data.recent_jobs.length ? (
            <ul className="list">
              {data.recent_jobs.map((j) => (
                <li key={j.id}><a href={href("jobs", j.id)} className="list-row">
                  <JobBadge status={j.status} /><span className="grow">{j.title}</span>
                  <span className="muted small">{fmtTime(j.created)} · {jobElapsed(j)}</span>
                </a></li>
              ))}
            </ul>
          ) : <div className="muted">还没有从网页提交过任务</div>}
        </section>
        <section className="card">
          <div className="card-head"><h2>最近 10 次运行耗时</h2><a href={href("history")} className="small">运行历史 →</a></div>
          <StackedBars bars={bars} height={200} />
          {data.history[0] ? <div className="muted small">最近一次：{data.history[0].project} · {fmtSec(data.history[0].total_sec)}</div> : null}
        </section>
      </div>
    </div>
  );
}
