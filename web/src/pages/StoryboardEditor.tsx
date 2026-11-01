import { useEffect, useState } from "react";
import { api, type Shot, type Storyboard } from "../api";
import { useToast } from "../hooks";
import { ErrorBox, Field, Loading } from "../components/ui";

const VOICES = ["Vivian", "Serena", "Uncle_Fu", "Eric", "Dylan"];

/** 分镜表编辑：表单和原始 JSON 两种方式。保存时后端用和命令行同一套规则校验。 */
export function StoryboardEditor({ file, onSaved }: { file: string; onSaved: () => void }) {
  const [sb, setSb] = useState<Storyboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.storyboard(file).then((r) => { setSb(r.data); setDirty(false); }, (e) => setError(e.message));
  }, [file]);
  // 有未保存的修改时，关闭页面要确认
  useEffect(() => {
    if (!dirty) return;
    const fn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [dirty]);

  if (error) return <ErrorBox error={error} />;
  if (!sb) return <Loading />;

  const update = (next: Storyboard) => { setSb(next); setDirty(true); };
  const setShot = (i: number, patch: Partial<Shot>) => update({ ...sb, shots: sb.shots.map((s, k) => (k === i ? clean({ ...s, ...patch }) : s)) });
  const move = (i: number, d: number) => {
    const shots = sb.shots.slice();
    const [x] = shots.splice(i, 1);
    shots.splice(i + d, 0, x);
    update({ ...sb, shots });
  };
  const nextId = () => Math.max(0, ...sb.shots.map((s) => (typeof s.id === "number" ? s.id : 0))) + 1;
  const chars = sb.characters || {};

  const save = async () => {
    let data = sb;
    if (raw !== null) {
      try { data = JSON.parse(raw); } catch (e) { toast(`JSON 格式不对：${(e as Error).message}`, "fail"); return; }
    }
    setSaving(true);
    try {
      const r = await api.saveStoryboard(file, data);
      setSb(r.data);
      if (raw !== null) setRaw(JSON.stringify(r.data, null, 2));
      setDirty(false);
      toast("已保存（改了画面描述的镜头，要重画才会生效）", "ok");
      onSaved();
    } catch (e) {
      toast(`没保存：${(e as Error).message}`, "fail");
    } finally { setSaving(false); }
  };

  return (
    <div className="stack">
      <div className="sticky-bar">
        <button className="btn primary" disabled={!dirty || saving} onClick={save}>{saving ? "保存中…" : dirty ? "保存修改" : "已保存"}</button>
        <button className="btn ghost" onClick={() => setRaw(raw === null ? JSON.stringify(sb, null, 2) : null)}>
          {raw === null ? "改原始 JSON" : "回到表单"}
        </button>
        <span className="muted small">画面描述里用 {"{角色id}"} 引用角色，会自动替换成角色的外貌描述，保证每个镜头长得一样。</span>
      </div>

      {raw !== null ? (
        <textarea className="json-editor" value={raw} spellCheck={false} onChange={(e) => { setRaw(e.target.value); setDirty(true); }} />
      ) : (
        <>
          <section className="card">
            <div className="row wrap">
              <Field label="标题" hint="输出文件夹按标题命名，改标题等于换了一个输出目录">
                <input value={sb.title} onChange={(e) => update({ ...sb, title: e.target.value })} />
              </Field>
              <Field label="默认音色">
                <select value={sb.defaults?.voice ?? ""} onChange={(e) => update({ ...sb, defaults: { ...sb.defaults, voice: e.target.value } })}>
                  <option value="">（配置里的默认）</option>
                  {VOICES.map((v) => <option key={v}>{v}</option>)}
                </select>
              </Field>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>角色</h2>
              <button className="btn small" onClick={() => {
                const id = prompt("角色 id（小写英文，镜头描述里写 {id} 引用）", "");
                if (!id) return;
                if (!/^[A-Za-z_][\w-]*$/.test(id)) { toast("id 只能用英文字母、数字、下划线", "fail"); return; }
                update({ ...sb, characters: { ...chars, [id]: { name: id, look: "" } } });
              }}>+ 角色</button>
            </div>
            {Object.keys(chars).length ? Object.entries(chars).map(([cid, c]) => (
              <div key={cid} className="char-row">
                <div className="char-id"><code>{`{${cid}}`}</code></div>
                <Field label="名字"><input value={c.name ?? ""} onChange={(e) => update({ ...sb, characters: { ...chars, [cid]: { ...c, name: e.target.value } } })} /></Field>
                <Field label="外貌（英文）" hint="年龄、脸型、发型、服装">
                  <textarea rows={2} value={c.look} onChange={(e) => update({ ...sb, characters: { ...chars, [cid]: { ...c, look: e.target.value } } })} />
                </Field>
                <button className="btn small ghost danger" title="删除角色" onClick={() => {
                  if (!confirm(`删除角色 ${cid}？镜头里用到 {${cid}} 的地方要一起改掉，否则保存不了。`)) return;
                  const next = { ...chars };
                  delete next[cid];
                  update({ ...sb, characters: next });
                }}>删除</button>
              </div>
            )) : <div className="muted small">没有角色。有固定人物的剧最好加上：定妆照、人脸质检、参考图生成都靠它。</div>}
          </section>

          {sb.shots.map((s, i) => (
            <section key={i} className="card shot-edit">
              <div className="card-head">
                <h2>镜头 {String(s.id).padStart(2, "0")}</h2>
                <div className="row">
                  <button className="btn small ghost" disabled={i === 0} onClick={() => move(i, -1)} title="上移">↑</button>
                  <button className="btn small ghost" disabled={i === sb.shots.length - 1} onClick={() => move(i, 1)} title="下移">↓</button>
                  <button className="btn small ghost danger" onClick={() => confirm(`删除镜头 ${s.id}？`) && update({ ...sb, shots: sb.shots.filter((_, k) => k !== i) })}>删除</button>
                </div>
              </div>
              <Field label="画面描述（英文，给出图模型）">
                <textarea rows={3} value={s.image} onChange={(e) => setShot(i, { image: e.target.value })} />
              </Field>
              {Object.keys(chars).length ? (
                <div className="row wrap small">
                  插入角色：{Object.keys(chars).map((cid) => (
                    <button key={cid} className="btn small ghost" onClick={() => setShot(i, { image: `${s.image} {${cid}}` })}>{`{${cid}}`}</button>
                  ))}
                </div>
              ) : null}
              <Field label="动作描述（英文，给图生视频）" hint="写画面里能看到的变化：动作、表情、镜头运动">
                <textarea rows={2} value={s.motion ?? ""} onChange={(e) => setShot(i, { motion: e.target.value })}
                  placeholder="{linxia} slowly turns her head and glances back, camera pushes in slightly" />
              </Field>
              <div className="row wrap">
                <Field label="台词 / 旁白"><input className="wide" value={s.line ?? ""} onChange={(e) => setShot(i, { line: e.target.value })} /></Field>
                <Field label="音色">
                  <select value={s.voice ?? ""} onChange={(e) => setShot(i, { voice: e.target.value || undefined })}>
                    <option value="">（默认）</option>
                    {VOICES.map((v) => <option key={v}>{v}</option>)}
                  </select>
                </Field>
                <Field label="语气"><input value={s.emotion ?? ""} onChange={(e) => setShot(i, { emotion: e.target.value })} placeholder="紧张，压低声音" /></Field>
                <Field label="时长（秒）" hint="空 = 按配音"><input type="number" step="0.1" min="0.5" value={s.duration ?? ""} onChange={(e) => setShot(i, { duration: e.target.value ? +e.target.value : undefined })} /></Field>
                <Field label="seed" hint="空 = 随机"><input type="number" value={s.seed ?? ""} onChange={(e) => setShot(i, { seed: e.target.value ? +e.target.value : undefined })} /></Field>
              </div>
            </section>
          ))}
          <button className="btn" onClick={() => update({ ...sb, shots: [...sb.shots, { id: nextId(), image: "Cinematic vertical film still of ...", motion: "", line: "" }] })}>+ 加一个镜头</button>
        </>
      )}
    </div>
  );
}

/** 去掉空字段，保存出来的 JSON 和手写的一样干净。 */
function clean(s: Shot): Shot {
  const out = { ...s } as Record<string, unknown>;
  for (const k of ["voice", "emotion", "duration", "seed"]) if (out[k] === "" || out[k] === undefined) delete out[k];
  return out as unknown as Shot;
}
