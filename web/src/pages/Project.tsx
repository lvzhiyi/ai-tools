import { useMemo, useState } from "react";
import { api, fmtSec, type Job, type Project, type ShotView } from "../api";
import { go, href, useAsync, useJobStream, useJobs, useOnProjectJobFinished, useToast, useModelRouting } from "../hooks";
import { ErrorBox, FaceScore, JobBadge, Loading, LogView, Markdown, Progress, Tabs, jobElapsed } from "../components/ui";
import { StackedBars } from "../components/StackedBars";
import { StoryboardEditor } from "./StoryboardEditor";
import { LabelPanel } from "./Label";

const TABS: [string, string][] = [
  ["shots", "镜头墙"], ["generate", "生成"], ["edit", "编辑分镜"], ["cast", "定妆照"],
  ["preview", "预览视频"], ["reports", "报告"], ["label", "人工标注"],
];

export function ProjectPage({ file, tab }: { file: string; tab: string }) {
  const { data, error, reload } = useAsync(() => api.project(file), [file]);
  useOnProjectJobFinished(file, reload);
  const { jobs } = useJobs();
  const busy = jobs.find((j) => j.project === file && (j.status === "running" || j.status === "queued"));

  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const p = data;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <a href={href("projects")} className="small muted">← 项目</a>
          <h1>{p.title}</h1>
          <div className="muted small">{p.file} · {p.shots.length} 个镜头 · 输出在 {p.dir}/</div>
        </div>
        {busy ? (
          <a className="busy" href={href("jobs", busy.id)}><JobBadge status={busy.status} /> {busy.title} {busy.status === "running" ? jobElapsed(busy) : ""}</a>
        ) : null}
      </div>
      <Tabs tabs={TABS} active={tab} onChange={(k) => go("p", file, k)} />
      {tab === "shots" && <ShotWall p={p} />}
      {tab === "generate" && <Generate p={p} />}
      {tab === "edit" && <StoryboardEditor file={file} onSaved={reload} />}
      {tab === "cast" && <Cast p={p} />}
      {tab === "preview" && <Preview p={p} />}
      {tab === "reports" && <Reports p={p} />}
      {tab === "label" && <LabelPanel file={file} />}
    </div>
  );
}

// ------------------------------------------------------------------ 镜头墙
function ShotWall({ p }: { p: Project }) {
  const done = p.shots.filter((s) => s.image).length;
  if (!done) {
    return (
      <div className="empty">
        <div className="empty-title">还没有出图</div>
        到「生成」里点「跑全流程」，或者先到「编辑分镜」检查一下画面描述。
        <div><button className="btn primary" onClick={() => go("p", p.file, "generate")}>去生成</button></div>
      </div>
    );
  }
  return <div className="shot-grid">{p.shots.map((s) => <ShotCard key={s.name} s={s} file={p.file} />)}</div>;
}

function ShotCard({ s, file }: { s: ShotView; file: string }) {
  const [view, setView] = useState<"clip" | "image">(s.clip ? "clip" : "image");
  const [cand, setCand] = useState<string | null>(null);
  const { jobs } = useJobs();
  const toast = useToast();
  const { refresh } = useJobs();
  const routing = useModelRouting();
  const routingBlocked = routing.loading || !!routing.pending || !!routing.error;
  const busy = routingBlocked || jobs.some((j) => j.project === file && (j.status === "running" || j.status === "queued"));
  const redo = async (kind: "image" | "audio" | "clip", what: string) => {
    if (routingBlocked) { toast("模型模式尚未就绪，请先完成切换或重新读取", "info"); return; }
    if (!confirm(`${what}镜头 ${s.name}？${kind === "clip" ? "会换一个随机种子，出不同的动作。" : "旧文件会移到 _history，不会删除。"}`)) return;
    try {
      const r = await api.redo(file, kind, s.name);
      toast(`已加入队列：${r.job.title}`, "info", href("jobs", r.job.id));
      refresh();
    } catch (e) { toast((e as Error).message, "fail"); }
  };
  const { submit } = useJobs();
  const qa = s.qa;
  const clipFace = s.i2v?.face;
  const flag = (qa && !qa.passed) || qa?.extreme_expression || s.i2v?.review;

  return (
    <article className={`shot ${flag ? "flag" : ""}`}>
      <header className="shot-head">
        <b>镜头 {s.name}</b>
        <span className="muted small">{s.characters.join("、") || "空镜"}{s.timeline ? ` · ${s.timeline.duration.toFixed(1)} 秒` : ""}</span>
        {s.clip && s.image ? (
          <span className="seg">
            <button className={view === "clip" ? "on" : ""} onClick={() => setView("clip")}>视频</button>
            <button className={view === "image" ? "on" : ""} onClick={() => setView("image")}>图</button>
          </span>
        ) : null}
      </header>
      <div className="media">
        {cand ? <img src={cand} alt="" /> : view === "clip" && s.clip ? (
          <video src={s.clip} poster={s.image ?? undefined} controls loop muted playsInline preload="metadata" />
        ) : s.image ? <img src={s.image} alt={`镜头 ${s.name}`} loading="lazy" /> : <div className="media-empty">还没出图</div>}
      </div>
      {s.candidates.length > 1 ? (
        <div className="cands" onMouseLeave={() => setCand(null)}>
          <span className="muted small">候选</span>
          {s.candidates.map((c) => <img key={c.name} src={c.url} alt={c.name} title={c.name} onMouseEnter={() => setCand(c.url)} />)}
        </div>
      ) : null}

      <div className="shot-body">
        {s.line ? <div className="line">「{s.line}」<span className="muted small">{s.voice}{s.emotion ? ` · ${s.emotion}` : ""}</span></div> : <div className="muted small">无台词</div>}
        {s.audio ? <audio src={s.audio} controls preload="none" /> : null}

        <div className="qa">
          {qa ? (
            <>
              <span className={`chip ${qa.passed ? "" : "bad"}`}>{qa.passed ? "✓ 质检通过" : "✕ 未达标"}{qa.attempts > 1 ? ` · 画了 ${qa.attempts} 次` : ""}</span>
              {Object.entries(qa.face).map(([cid, v]) => <span key={cid} className="chip"><FaceScore value={v} label={`人脸 ${cid}`} /></span>)}
              {qa.match_score ? <span className="chip">画面 {qa.match_score}/5</span> : null}
              {qa.extreme_expression ? <span className="chip warn">👀 表情夸张，建议人工看</span> : null}
              <span className="chip muted">{qa.mode}</span>
            </>
          ) : <span className="muted small">没有质检记录</span>}
        </div>
        {s.i2v ? (
          <div className="qa">
            <span className="chip">视频 {fmtSec(s.i2v.sec)} · {s.i2v.video_sec} 秒{s.i2v.seed ? ` · seed ${s.i2v.seed}` : ""}</span>
            {clipFace && clipFace.identity_min !== undefined ? (
              <span className={`chip ${s.i2v.review ? "warn" : ""}`}>
                动起来后人脸 {clipFace.identity_min.toFixed(2)}~{clipFace.identity_median?.toFixed(2)}{s.i2v.review ? " 👀" : ""}
                <span className="muted"> · 比了 {clipFace.frames} 帧</span>
              </span>
            ) : clipFace ? <span className="chip warn">没有可比对的正脸帧 👀</span> : null}
          </div>
        ) : null}
        {qa?.defects?.length ? <div className="muted small">瑕疵：{qa.defects.join("；")}</div> : null}

        <details className="prompt">
          <summary>画面 / 动作描述</summary>
          <div className="small">{s.image_prompt}</div>
          {s.motion ? <div className="small motion">动作：{s.motion}</div> : <div className="small muted">没写动作（motion），图生视频只会轻微晃动</div>}
        </details>

        <div className="actions">
          <button className="btn small" disabled={busy} onClick={() => redo("image", "重画")}>重画图</button>
          {s.line ? <button className="btn small" disabled={busy} onClick={() => redo("audio", "重新配音")}>重新配音</button> : null}
          {s.clip ? <button className="btn small" disabled={busy} onClick={() => redo("clip", "重做视频片段：")}>重做视频</button>
            : s.image ? <button className="btn small" disabled={busy} onClick={() => submit("i2v", file, { shots: [s.name] })}>生成视频</button> : null}
        </div>
      </div>
    </article>
  );
}

// ------------------------------------------------------------------ 生成
function Generate({ p }: { p: Project }) {
  const { submit, jobs } = useJobs();
  const [opts, setOpts] = useState({ force: false, no_qa: false, no_edit: false, i2v: false, no_vision: false });
  const projectJobs = jobs.filter((j) => j.project === p.file);
  const current = projectJobs.find((j) => j.status === "running") ?? projectJobs.find((j) => j.status === "queued") ?? projectJobs[0];
  const [watch, setWatch] = useState<string | null>(null);
  const shown = watch ?? current?.id ?? null;
  const run = (only?: string) => submit("run", p.file, { only, force: opts.force, no_qa: opts.no_qa, no_edit: opts.no_edit, i2v: opts.i2v })
    .then((j) => j && setWatch(j.id));
  const missing = {
    images: p.shots.filter((s) => !s.image).length,
    audio: p.shots.filter((s) => s.line && !s.audio).length,
    clips: p.shots.filter((s) => s.image && !s.clip).length,
  };
  const toggle = (k: keyof typeof opts, label: string, hint: string) => (
    <label className="check-opt" title={hint}>
      <input type="checkbox" checked={opts[k]} onChange={(e) => setOpts({ ...opts, [k]: e.target.checked })} />{label}
      <span className="hint">{hint}</span>
    </label>
  );

  return (
    <div className="stack">
      <section className="card">
        <h2>一键生成</h2>
        <p className="muted small">每一步都会跳过已有的文件，只补缺的；勾「全部重新生成」才会覆盖。任务一次只跑一个（24G 内存放不下两个大模型），后提交的会排队。</p>
        <div className="row wrap">
          <button className="btn primary" onClick={() => run()}>跑全流程{opts.i2v ? "（含图生视频）" : ""}</button>
          {toggle("i2v", "包含图生视频", "每段约 1~2 分钟")}
          {toggle("force", "全部重新生成", "覆盖已有文件")}
          {toggle("no_qa", "关闭质检", "每个镜头只画一次")}
          {toggle("no_edit", "不用参考图", "全部文生图，做对比实验用")}
        </div>
      </section>

      <section className="card">
        <h2>单独跑某一步</h2>
        <div className="step-buttons">
          <StepButton title="定妆照" desc="角色正脸 + 左右侧脸" onClick={() => submit("cast", p.file, { force: opts.force }).then((j) => j && setWatch(j.id))} />
          <StepButton title="出图 + 质检" desc={missing.images ? `缺 ${missing.images} 张` : "都有了"} onClick={() => run("images")} />
          <StepButton title="配音" desc={missing.audio ? `缺 ${missing.audio} 句` : "都有了"} onClick={() => run("audio")} />
          <StepButton title="图生视频" desc={missing.clips ? `缺 ${missing.clips} 段` : "都有了"}
            onClick={() => submit("i2v", p.file, { force: opts.force }).then((j) => j && setWatch(j.id))} />
          <StepButton title="字幕" desc="按台词 + 配音时长" onClick={() => run("subtitle")} />
          <StepButton title="预览视频" desc="有片段用片段" onClick={() => run("video")} />
          <StepButton title="只质检" desc="不重画，重出报告"
            onClick={() => submit("qa", p.file, { no_vision: opts.no_vision }).then((j) => j && setWatch(j.id))} />
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>任务日志</h2>
          <select value={shown ?? ""} onChange={(e) => setWatch(e.target.value || null)}>
            {projectJobs.length ? projectJobs.map((j) => <option key={j.id} value={j.id}>{j.title} · {j.id}</option>) : <option value="">还没有任务</option>}
          </select>
        </div>
        {shown ? <JobPanel id={shown} /> : <div className="muted">提交任务后，日志会实时显示在这里。</div>}
      </section>
    </div>
  );
}

const StepButton = ({ title, desc, onClick }: { title: string; desc: string; onClick: () => void }) => (
  <button className="step-btn" onClick={onClick}><b>{title}</b><span className="muted small">{desc}</span></button>
);

export function JobPanel({ id, height }: { id: string; height?: number }) {
  const { job, lines } = useJobStream(id);
  const toast = useToast();
  return (
    <div className="stack tight">
      {job ? (
        <div className="row">
          <JobBadge status={job.status} />
          <span>{job.title}</span>
          {job.stage ? <span className="muted">· {job.stage}</span> : null}
          {job.progress ? <span className="muted">· {job.progress[0]}/{job.progress[1]}</span> : null}
          <span className="muted">· {jobElapsed(job)}</span>
          <span className="grow" />
          {(job.status === "running" || job.status === "queued") ? (
            <button className="btn small danger" onClick={() => api.cancel(job.id).then(() => toast("正在取消…"), (e) => toast(e.message, "fail"))}>取消</button>
          ) : null}
        </div>
      ) : null}
      {job?.status === "running" ? <Progress job={job as Job} /> : null}
      <code className="cmd">{job?.command}</code>
      <LogView lines={lines} height={height} />
    </div>
  );
}

// ------------------------------------------------------------------ 定妆照
function Cast({ p }: { p: Project }) {
  const { submit } = useJobs();
  if (!p.cast.length) return <div className="empty"><div className="empty-title">这个分镜表没有定义角色</div>到「编辑分镜」里加角色，镜头描述里用 {"{角色id}"} 引用。</div>;
  return (
    <div className="stack">
      <div className="row">
        <button className="btn" onClick={() => submit("cast", p.file, {})}>补齐定妆照</button>
        <button className="btn" onClick={() => confirm("重画所有角色的定妆照？之后的镜头质检都会以新定妆照为准。") && submit("cast", p.file, { force: true })}>全部重画</button>
        <span className="muted small">定妆照是质检比对的"标准脸"，也是参考图生成时喂给模型的参考。</span>
      </div>
      {p.cast.map((c) => (
        <section key={c.id} className="card">
          <h2>{c.name} <span className="muted small">{c.id}</span></h2>
          <p className="small muted">{c.look}</p>
          <div className="cast-row">
            {c.images.length ? c.images.map((img) => (
              <figure key={img.url}><img src={img.url} alt={img.label} /><figcaption>{img.label}</figcaption></figure>
            )) : <div className="muted">还没生成定妆照</div>}
          </div>
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ 预览
function Preview({ p }: { p: Project }) {
  const { submit } = useJobs();
  const total = p.shots.reduce((a, s) => a + (s.timeline?.duration || 0), 0);
  return (
    <div className="preview-layout">
      <div className="phone">
        {p.preview ? (
          <video src={p.preview} controls playsInline>
            {p.subtitles ? <track kind="subtitles" src={p.subtitles} srcLang="zh" label="中文" default /> : null}
          </video>
        ) : <div className="media-empty">还没有预览视频</div>}
      </div>
      <section className="card grow">
        <div className="card-head"><h2>时间线</h2><button className="btn small" onClick={() => submit("run", p.file, { only: "video" })}>重新拼预览</button></div>
        <div className="timeline">
          {p.shots.map((s) => s.timeline ? (
            <div key={s.name} className="tl-item" style={{ flex: s.timeline.duration }} title={`镜头 ${s.name}：${s.timeline.duration.toFixed(1)} 秒`}>
              {s.image ? <img src={s.image} alt="" /> : null}
              <span>{s.name}{s.clip ? " ▶" : ""}</span>
            </div>
          ) : null)}
        </div>
        <div className="muted small">共 {total.toFixed(1)} 秒；▶ 表示这个镜头用的是动态片段。镜头长度 = 台词配音时长 + 留白，没有台词的镜头默认 2 秒。</div>
        <table className="table">
          <thead><tr><th>镜头</th><th>开始</th><th>时长</th><th>台词</th></tr></thead>
          <tbody>{p.shots.map((s) => (
            <tr key={s.name}><td>{s.name}</td><td>{s.timeline?.start.toFixed(1) ?? "-"}</td><td>{s.timeline?.duration.toFixed(1) ?? "-"}</td><td>{s.line || <span className="muted">—</span>}</td></tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ 报告
function Reports({ p }: { p: Project }) {
  const [run, setRun] = useState<string | null>(p.runs[0]?.id ?? null);
  const report = useAsync(() => (run ? api.runReport(p.file, run) : Promise.resolve("")), [p.file, run]);
  const bars = useMemo(() => p.runs.slice().reverse().map((r) => ({
    id: r.id, label: r.started?.slice(5, 16) ?? r.id, sub: r.command?.replace(/^studio /, ""), status: r.status, values: r.stage_sec,
  })), [p.runs]);
  return (
    <div className="stack">
      <section className="card">
        <h2>这个项目的运行耗时</h2>
        <StackedBars bars={bars} />
      </section>
      <div className="split">
        <section className="card side-list">
          <h2>运行记录</h2>
          <ul className="list">
            {p.runs.map((r) => (
              <li key={r.id}><button className={`list-row ${run === r.id ? "on" : ""}`} onClick={() => setRun(r.id)}>
                <span className={`dot ${r.status === "ok" ? "ok" : "fail"}`} aria-label={r.status} />
                <span className="grow">{r.started?.slice(5, 16)}<div className="muted small">{r.command?.replace(/^studio /, "")}</div></span>
                <span className="muted small">{fmtSec(r.total_sec)}</span>
              </button></li>
            ))}
          </ul>
        </section>
        <section className="card grow">
          {report.data ? <Markdown text={report.data} /> : <div className="muted">选择左边的一次运行</div>}
        </section>
      </div>
      <section className="card">
        <h2>质检报告</h2>
        {p.qa_report ? <Markdown text={p.qa_report.replace(/\]\((?!http)([^)]+)\)/g, (_, path) => `](/files/${path.replace(/^output\//, "")})`)} /> : <div className="muted">还没有质检报告（出图时自动生成，或在「生成」里点「只质检」）</div>}
      </section>
    </div>
  );
}
