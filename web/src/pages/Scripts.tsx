import { useEffect, useState } from "react";
import { api, fmtTime } from "../api";
import { go, href, useAsync, useJobs, useOnProjectJobFinished, useToast } from "../hooks";
import { Field, Loading } from "../components/ui";

export function Scripts({ selected }: { selected?: string }) {
  const list = useAsync(() => api.scripts(), []);
  const { submit, jobs } = useJobs();
  const toast = useToast();
  const [name, setName] = useState(selected?.replace(/\.txt$/, "") || "");
  const [text, setText] = useState("");
  const [maxShots, setMaxShots] = useState(12);
  const [dirty, setDirty] = useState(false);
  useOnProjectJobFinished(null, list.reload);

  useEffect(() => {
    if (!selected) return;
    setName(selected.replace(/\.txt$/, ""));
    api.script(selected).then((t) => { setText(t); setDirty(false); }).catch((e) => toast(e.message, "fail"));
  }, [selected, toast]);

  const save = async () => {
    const r = await api.saveScript(name, text);
    setDirty(false);
    list.reload();
    return r.file;
  };
  const plan = async () => {
    try {
      const file = await save();
      const job = await submit("plan", null, { script: file, max_shots: maxShots });
      if (job) go("jobs", job.id);
    } catch (e) { toast((e as Error).message, "fail"); }
  };
  const planning = jobs.some((j) => j.kind === "plan" && (j.status === "running" || j.status === "queued"));

  return (
    <div className="stack">
      <div className="page-head"><h1>剧本</h1></div>
      <div className="split">
        <section className="card side-list">
          <div className="card-head"><h2>已有剧本</h2>
            <button className="btn small" onClick={() => { setName(""); setText(""); go("scripts"); }}>新剧本</button></div>
          {!list.data ? <Loading /> : (
            <ul className="list">
              {list.data.map((s) => (
                <li key={s.file}>
                  <a className={`list-row ${selected === s.file ? "on" : ""}`} href={href("scripts", s.file)}>
                    <span className="grow">{s.file}</span>
                    <span className="muted small">{s.chars} 字 · {fmtTime(s.updated).slice(5, 16)}</span>
                  </a>
                  {s.storyboard ? <a className="small sub-link" href={href("p", s.file.replace(/\.txt$/, ".json"))}>→ 分镜表 {s.file.replace(/\.txt$/, ".json")}</a> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card grow">
          <div className="row wrap">
            <Field label="剧本名" hint="拆出来的分镜表叫 <剧本名>.json">
              <input value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} placeholder="ep01" />
            </Field>
            <Field label="最多几个镜头"><input type="number" min={2} max={40} value={maxShots} onChange={(e) => setMaxShots(+e.target.value)} /></Field>
          </div>
          <textarea className="script-editor" value={text} onChange={(e) => { setText(e.target.value); setDirty(true); }}
            placeholder={"把故事或剧本粘贴到这里。\n\n例：雨夜，林夏从地铁站出来，总觉得有人跟着她……"} />
          <div className="row">
            <button className="btn" disabled={!name.trim() || !text.trim()} onClick={() => save().then(() => toast("已保存", "ok"), (e) => toast(e.message, "fail"))}>
              保存{dirty ? " *" : ""}
            </button>
            <button className="btn primary" disabled={!name.trim() || !text.trim() || planning} onClick={plan}>
              {planning ? "正在拆分镜…" : "用本地模型拆分镜"}
            </button>
            <span className="muted small">需要先打开 LM Studio 的 Server 并加载模型；拆完记得卸载模型，把内存让给出图。</span>
          </div>
        </section>
      </div>
    </div>
  );
}
