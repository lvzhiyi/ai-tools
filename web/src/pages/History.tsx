import { useMemo, useState } from "react";
import { api, fmtSec } from "../api";
import { useAsync } from "../hooks";
import { ErrorBox, Field, Loading } from "../components/ui";
import { StackedBars } from "../components/StackedBars";

/** 所有运行的历史（output/runs.csv）：改了模型、参数、代码之后，用数据看是快了还是慢了。 */
export function History() {
  const { data, error } = useAsync(() => api.history(), []);
  const [project, setProject] = useState("");
  const [command, setCommand] = useState("");
  const projects = useMemo(() => [...new Set((data ?? []).map((r) => r.project))], [data]);
  const rows = useMemo(() => (data ?? []).filter((r) => (!project || r.project === project) && (!command || r.command.includes(command))), [data, project, command]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const bars = rows.slice(-40).map((r) => ({
    id: r.time + r.command, label: r.time.slice(5, 16), sub: `${r.project} · ${r.command.replace(/^studio /, "")} · git ${r.git}`, status: r.status,
    values: { images: +r.images_sec || 0, audio: +r.audio_sec || 0, i2v: +r.i2v_sec || 0, subtitle: +r.subtitle_sec || 0, video: +r.video_sec || 0 },
  }));
  return (
    <div className="stack">
      <div className="page-head"><h1>运行历史</h1><span className="muted small">只有同一条命令的运行互相比较才有意义（跑的内容一样）。</span></div>
      <div className="row wrap filters">
        <Field label="项目">
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">全部</option>{projects.map((p) => <option key={p}>{p}</option>)}
          </select>
        </Field>
        <Field label="命令包含"><input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="--only images" /></Field>
      </div>
      <section className="card"><h2>各阶段耗时（最近 {bars.length} 次）</h2><StackedBars bars={bars} height={260} /></section>
      <section className="card">
        <table className="table compact">
          <thead><tr><th>时间</th><th>项目</th><th>命令</th><th>git</th><th>文生图</th><th>总耗时</th><th>每张图</th><th>一次通过</th><th>每秒视频</th></tr></thead>
          <tbody>{rows.slice().reverse().map((r, i) => (
            <tr key={i} className={r.status === "ok" ? "" : "failed-row"}>
              <td>{r.time.slice(5, 16)}</td><td>{r.project}</td><td><code className="small">{r.command.replace(/^studio /, "")}</code></td>
              <td className="muted small">{r.git}</td><td>{r.t2i}</td><td>{fmtSec(r.total_sec)}</td><td>{fmtSec(r.avg_image_sec)}</td>
              <td>{r.first_pass}</td><td>{r.i2v_sec_per_video_sec ? `${r.i2v_sec_per_video_sec} 秒` : "-"}</td>
            </tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  );
}
