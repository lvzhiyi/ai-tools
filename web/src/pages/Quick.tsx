import { useEffect, useState } from "react";
import { api } from "../api";
import { href, useJobs } from "../hooks";
import { Field } from "../components/ui";
import { JobPanel } from "./Project";

/** 只出一张图，调提示词用（studio image），结果在 output/_quick/。 */
export function Quick() {
  const { submit } = useJobs();
  const [prompt, setPrompt] = useState("Cinematic vertical film still of ");
  const [seed, setSeed] = useState("");
  const [job, setJob] = useState<string | null>(null);
  return (
    <div className="stack">
      <div className="page-head"><h1>试一张图</h1><span className="muted small">调提示词用：不质检、不进项目，图存在 output/_quick/。</span></div>
      <section className="card">
        <Field label="画面描述（英文）"><textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
        <div className="row">
          <Field label="seed" hint="空 = 随机"><input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} /></Field>
          <button className="btn primary" disabled={prompt.trim().length < 10}
            onClick={() => submit("image", null, { prompt, seed: seed ? +seed : undefined }).then((j) => j && setJob(j.id))}>出图</button>
        </div>
      </section>
      {job ? <section className="card"><JobPanel id={job} height={200} /><QuickResult id={job} /></section> : null}
      <div className="muted small">出好的图在任务日志最后一行；想在项目里用，把描述复制到 <a href={href("projects")}>项目</a> 的分镜里。</div>
    </div>
  );
}

function QuickResult({ id }: { id: string }) {
  const { jobs } = useJobs();
  const j = jobs.find((x) => x.id === id);
  if (!j || j.status !== "succeeded") return null;
  return <QuickImage id={id} />;
}

function QuickImage({ id }: { id: string }) {
  // 日志里有一行形如「✅ /…/output/_quick/20261101-120000.png（用时 40s）」
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    api.job(id).then((d) => {
      const line = (d.lines ?? []).slice().reverse().find((l) => l.includes("_quick/"));
      const m = line?.match(/output\/(_quick\/[^\s（]+\.png)/);
      setSrc(m ? `/files/${m[1]}?v=${Date.now()}` : null);
    }).catch(() => setSrc(null));
  }, [id]);
  return src ? <img className="quick-img" src={src} alt="出图结果" /> : null;
}
