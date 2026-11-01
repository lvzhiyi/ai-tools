import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type LabelPair } from "../api";
import { href, useToast } from "../hooks";
import { Empty, ErrorBox, Loading } from "../components/ui";

const VALUES: [string, string, string][] = [["same", "像，同一个人", "1"], ["diff", "不像", "2"], ["skip", "说不准", "3"]];

/** 人工标注："如果这两张出现在同一部剧里，是同一个角色吗？" —— 校准阈值用的正确答案。
 *  刻意的设计（和 studio label 一样）：不显示分数（盲标）、固定打乱顺序、每点一次就保存。 */
export function LabelPanel({ file }: { file: string }) {
  const [pairs, setPairs] = useState<LabelPair[] | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [stale, setStale] = useState(0);
  const [i, setI] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.labels(file).then((d) => {
      setPairs(d.pairs);
      setLabels(d.labels);
      setStale(d.stale);
      const first = d.pairs.findIndex((p) => !d.labels[p.key]);
      setI(first < 0 ? d.pairs.length : first);
    }, (e) => setError(e.message));
  }, [file]);

  const mark = useCallback(async (v: string) => {
    if (!pairs || i >= pairs.length) return;
    const p = pairs[i];
    setLabels((l) => ({ ...l, [p.key]: v }));
    setI((x) => Math.min(pairs.length, x + 1));
    try { await api.label(file, p.key, v); } catch (e) { toast(`没保存：${(e as Error).message}`, "fail"); }
  }, [pairs, i, file, toast]);

  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      const hit = VALUES.find(([, , k]) => k === e.key);
      if (hit) mark(hit[0]);
      else if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
      else if (e.key === "ArrowRight" && pairs) setI((x) => Math.min(pairs.length, x + 1));
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [mark, pairs]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { same: 0, diff: 0, skip: 0 };
    Object.values(labels).forEach((v) => { c[v] = (c[v] || 0) + 1; });
    return c;
  }, [labels]);

  if (error) return <ErrorBox error={error} />;
  if (!pairs) return <Loading />;
  if (!pairs.length) return <Empty title="没有可标注的图">先跑一次带质检的出图，生成定妆照和候选图。</Empty>;
  const done = pairs.filter((p) => labels[p.key]).length;
  const p = pairs[i];

  return (
    <div className="stack">
      <div className="label-head">
        <div>如果这两张出现在同一部剧里，你会觉得是<b className="hl">同一个角色</b>吗？<span className="muted small">（只看脸，衣服发型本来就一样）</span></div>
        <div className="muted small">已标 {done}/{pairs.length} · 像 {counts.same} · 不像 {counts.diff} · 说不准 {counts.skip}
          {stale ? ` · ${stale} 条旧标注对应的图已重画，已忽略` : ""}</div>
      </div>
      <div className="progress"><div className="progress-fill" style={{ width: `${(done / pairs.length) * 100}%` }} /></div>
      {p ? (
        <>
          <div className="pair">
            <figure><img src={p.a_url} alt="" /><figcaption>{p.a.split("/").pop()}</figcaption></figure>
            <figure><img src={p.b_url} alt="" /><figcaption>{p.b.split("/").pop()}</figcaption></figure>
          </div>
          <div className="row center">
            <button className="btn" onClick={() => setI(Math.max(0, i - 1))}>← 上一对</button>
            {VALUES.map(([v, text, key]) => (
              <button key={v} className={`btn big label-${v} ${labels[p.key] === v ? "on" : ""}`} onClick={() => mark(v)}>{key} · {text}</button>
            ))}
          </div>
          <div className="muted small center">快捷键：1 像 · 2 不像 · 3 说不准 · ← → 翻页。页面不显示相似度分数，避免被数字影响判断。</div>
        </>
      ) : (
        <Empty title={`✅ 全部标完（${done} 对）`}>
          <div className="row center">
            <button className="btn" onClick={() => setI(pairs.length - 1)}>← 回看上一对</button>
            <a className="btn primary" href={href("calibrate")}>去校准阈值</a>
          </div>
        </Empty>
      )}
    </div>
  );
}
