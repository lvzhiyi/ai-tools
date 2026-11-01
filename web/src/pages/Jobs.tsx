import { api, fmtTime } from "../api";
import { href, useAsync, useJobs } from "../hooks";
import { Empty, ErrorBox, JobBadge, Loading, jobElapsed } from "../components/ui";
import { JobPanel } from "./Project";

export function Jobs() {
  const { jobs } = useJobs();
  return (
    <div className="stack">
      <div className="page-head"><h1>任务</h1>
        <span className="muted small">网页上的每个操作都是一条 studio 命令，在后台排队执行；一次只跑一个。</span></div>
      {!jobs.length ? <Empty title="还没有任务">在项目页点「生成」，或者在「剧本」里拆分镜。</Empty> : (
        <section className="card">
          <table className="table">
            <thead><tr><th>状态</th><th>任务</th><th>命令</th><th>提交时间</th><th>用时</th></tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="clickable" onClick={() => (window.location.hash = href("jobs", j.id).slice(1))}>
                  <td><JobBadge status={j.status} /></td>
                  <td>{j.title}{j.status === "running" && j.progress ? <span className="muted small"> · {j.progress[0]}/{j.progress[1]}</span> : null}</td>
                  <td><code className="small">{j.command}</code></td>
                  <td className="muted small">{fmtTime(j.created)}</td>
                  <td className="muted small">{jobElapsed(j)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

export function JobDetail({ id }: { id: string }) {
  const { data, error } = useAsync(() => api.job(id), [id]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  // 拆分镜任务：成功后直接给出新分镜表的入口
  const commandArgs = data.argv[0] === "--llm-mode" ? data.argv.slice(2) : data.argv;
  const planned = data.kind === "plan" ? commandArgs[1]?.replace(/^screenplays\//, "").replace(/\.txt$/, ".json") : null;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <a href={href("jobs")} className="small muted">← 任务</a>
          <h1>{data.title}</h1>
          <div className="muted small">提交于 {fmtTime(data.created)} · 任务 {data.id}</div>
        </div>
        <div className="row">
          {data.project ? <a className="btn" href={href("p", data.project)}>打开项目</a> : null}
          {planned ? <a className="btn primary" href={href("p", planned, "edit")}>打开拆好的分镜表</a> : null}
        </div>
      </div>
      <section className="card"><JobPanel id={id} height={520} /></section>
    </div>
  );
}
