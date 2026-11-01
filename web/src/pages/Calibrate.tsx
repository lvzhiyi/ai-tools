import { useState } from "react";
import { api, fmtTime } from "../api";
import { href, useAsync, useJobs, useOnProjectJobFinished } from "../hooks";
import { Empty, ErrorBox, Field, Loading, Markdown } from "../components/ui";

/** 阈值校准：人工标注（像 / 不像）+ 对照组（不同的人），算出误拒率、误收率和建议阈值。 */
export function Calibrate() {
  const cal = useAsync(() => api.calibrations(), []);
  const sbs = useAsync(() => api.storyboards(), []);
  const cfg = useAsync(() => api.config(), []);
  const { submit } = useJobs();
  const [file, setFile] = useState("calibration.json");
  const [people, setPeople] = useState(6);
  useOnProjectJobFinished(null, cal.reload);
  const current = (cfg.data?.qa as { face_threshold?: number } | undefined)?.face_threshold;

  return (
    <div className="stack">
      <div className="page-head"><h1>阈值校准</h1></div>
      <section className="card">
        <h2>怎么做</h2>
        <ol className="steps-list">
          <li>用校准专用分镜表（同一角色、8 种景别和表情）出图，<b>关掉质检</b>，拿到没筛选过的样本。</li>
          <li>到这个项目的「人工标注」里，判断每两张图像不像同一个人 —— 这是"正确答案"。</li>
          <li>运行校准：再生成几个"不同的人"作对照组，对比人工判断，算出不同阈值下的误拒率和误收率。</li>
        </ol>
        <div className="row wrap">
          <Field label="分镜表">
            <select value={file} onChange={(e) => setFile(e.target.value)}>
              {(sbs.data ?? []).map((s) => <option key={s.file} value={s.file}>{s.title}（{s.file}）</option>)}
            </select>
          </Field>
          <Field label="对照组人数" hint="每人约 1 分钟"><input type="number" min={2} max={8} value={people} onChange={(e) => setPeople(+e.target.value)} /></Field>
          <button className="btn" onClick={() => submit("run", file, { only: "images", no_qa: true })}>① 出图（关质检）</button>
          <a className="btn" href={href("p", file, "label")}>② 人工标注</a>
          <button className="btn primary" onClick={() => submit("calibrate", file, { people })}>③ 运行校准</button>
        </div>
        <div className="muted small">当前 config.toml 里的人脸阈值：<b>{current ?? "-"}</b>（校准结果不会自动改配置，确认后手动改）</div>
      </section>

      {cal.error ? <ErrorBox error={cal.error} /> : !cal.data ? <Loading /> : !cal.data.length ? (
        <Empty title="还没有校准结果" />
      ) : cal.data.map((c) => (
        <section key={c.profile} className="card">
          <div className="card-head"><h2>出图模型：{c.profile}</h2><span className="muted small">{fmtTime(c.updated)}</span></div>
          {c.summary.compare?.length ? (
            <table className="table compact">
              <thead><tr><th>阈值</th><th>误拒（同一个人被判成不像）</th><th>误收（不同的人被判成像）</th><th>和人工一致</th></tr></thead>
              <tbody>{c.summary.compare.map((r) => (
                <tr key={r.threshold} className={r.threshold === current ? "hl-row" : ""}>
                  <td>{r.threshold}{r.threshold === current ? "（当前）" : ""}</td>
                  <td>{pct(r.false_reject_rate)}</td><td>{pct(r.false_accept_rate)}</td>
                  <td>{r.agreement !== undefined ? pct(r.agreement) : "-"}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : null}
          {c.svg ? <img className="cal-svg" src={c.svg} alt="相似度分布图" /> : null}
          <details><summary>完整报告</summary><Markdown text={c.report.replace(/!\[[^\]]*\]\(distribution\.svg\)/, "")} /></details>
        </section>
      ))}
    </div>
  );
}

const pct = (v: number) => `${Math.round(v * 100)}%`;
