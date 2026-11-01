import { useState } from "react";
import { api, fmtTime } from "../api";
import { go, href, useAsync, useOnProjectJobFinished, useToast } from "../hooks";
import { Empty, ErrorBox, Field, Loading } from "../components/ui";

export function Projects() {
  const { data, error, reload } = useAsync(() => api.storyboards(), []);
  useOnProjectJobFinished(null, reload);
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack">
      <div className="page-head">
        <h1>项目</h1>
        <div className="row">
          <button className="btn" onClick={() => setCreating(!creating)}>新建空白分镜表</button>
          <a className="btn primary" href={href("scripts")}>从剧本拆分镜</a>
        </div>
      </div>
      {creating ? <NewStoryboard onDone={() => setCreating(false)} /> : null}
      {error ? <ErrorBox error={error} onRetry={reload} /> : !data ? <Loading /> : !data.length ? (
        <Empty title="还没有分镜表">在「剧本」里粘贴故事，让本地模型拆成分镜；或者新建一张空白分镜表手写。</Empty>
      ) : (
        <div className="project-grid">
          {data.map((p) => {
            const d = p.done;
            return (
              <a key={p.file} className="project-card" href={href("p", p.file)}>
                <div className="cover">{p.cover ? <img src={p.cover} alt="" loading="lazy" /> : <span className="muted">还没出图</span>}</div>
                <div className="project-body">
                  <div className="project-title">{p.title}</div>
                  <div className="muted small">{p.file} · {p.shots} 个镜头 · {p.characters} 个角色</div>
                  {p.error ? <div className="error-inline">⚠ {p.error}</div> : (
                    <div className="steps">
                      <Step label="图" n={d.images} total={p.shots} />
                      <Step label="配音" n={d.audio} total={d.lines} />
                      <Step label="视频" n={d.clips} total={p.shots} />
                      <span className={`step ${d.preview ? "done" : ""}`}>{d.preview ? "✓" : "○"} 预览</span>
                    </div>
                  )}
                  <div className="muted small">修改于 {fmtTime(p.updated)}</div>
                </div>
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

const Step = ({ label, n, total }: { label: string; n: number; total: number }) => (
  <span className={`step ${total && n >= total ? "done" : ""}`}>{total && n >= total ? "✓" : "○"} {label} {n}/{total}</span>
);

function NewStoryboard({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState("");
  const [title, setTitle] = useState("");
  const toast = useToast();
  const submit = async () => {
    try {
      const r = await api.newStoryboard(file, title || file);
      toast("已创建", "ok");
      onDone();
      go("p", r.file, "edit");
    } catch (e) { toast((e as Error).message, "fail"); }
  };
  return (
    <section className="card">
      <div className="row wrap">
        <Field label="文件名" hint="英文或中文，不用写 .json"><input value={file} onChange={(e) => setFile(e.target.value)} placeholder="ep01" /></Field>
        <Field label="标题" hint="输出文件夹按标题命名"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="第一集-雨夜" /></Field>
        <button className="btn primary" disabled={!file.trim()} onClick={submit}>创建并编辑</button>
      </div>
    </section>
  );
}
