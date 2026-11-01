import { createRoot } from "react-dom/client";
import "./styles.css";
import { Providers, href, useJobs, useRoute } from "./hooks";
import { Progress, jobElapsed } from "./components/ui";
import { Dashboard } from "./pages/Dashboard";
import { Projects } from "./pages/Projects";
import { ProjectPage } from "./pages/Project";
import { Scripts } from "./pages/Scripts";
import { JobDetail, Jobs } from "./pages/Jobs";
import { Calibrate } from "./pages/Calibrate";
import { History } from "./pages/History";
import { Quick } from "./pages/Quick";

const NAV: [string, string, string][] = [
  ["", "总览", "◎"],
  ["projects", "项目", "▤"],
  ["scripts", "剧本", "✎"],
  ["quick", "试一张图", "◐"],
  ["jobs", "任务", "≡"],
  ["history", "运行历史", "▥"],
  ["calibrate", "阈值校准", "⌖"],
];

function JobBar() {
  const { jobs } = useJobs();
  const running = jobs.find((j) => j.status === "running");
  const queued = jobs.filter((j) => j.status === "queued").length;
  if (!running && !queued) return <div className="jobbar idle">空闲 · 没有正在运行的任务</div>;
  return (
    <a className="jobbar" href={running ? href("jobs", running.id) : href("jobs")}>
      {running ? (
        <>
          <span className="pulse" />
          <b>{running.title}</b>
          <span className="muted">{running.stage ? `· ${running.stage}` : ""}{running.progress ? ` · ${running.progress[0]}/${running.progress[1]}` : ""} · {jobElapsed(running)}</span>
          <div className="jobbar-progress"><Progress job={running} /></div>
        </>
      ) : null}
      {queued ? <span className="muted">排队 {queued} 个</span> : null}
    </a>
  );
}

function App() {
  const route = useRoute();
  const [page, ...rest] = route;
  let body;
  switch (page ?? "") {
    case "": body = <Dashboard />; break;
    case "projects": body = <Projects />; break;
    case "p": body = <ProjectPage file={rest[0]} tab={rest[1] || "shots"} />; break;
    case "scripts": body = <Scripts selected={rest[0]} />; break;
    case "quick": body = <Quick />; break;
    case "jobs": body = rest[0] ? <JobDetail id={rest[0]} /> : <Jobs />; break;
    case "history": body = <History />; break;
    case "calibrate": body = <Calibrate />; break;
    default: body = <div className="pad">页面不存在</div>;
  }
  const active = page === "p" ? "projects" : page ?? "";
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">ai-tools<span>本地 AI 短剧工作台</span></div>
        <nav>
          {NAV.map(([k, label, icon]) => (
            <a key={k} href={href(...(k ? [k] : []))} className={active === k ? "on" : ""}>
              <span className="icon" aria-hidden>{icon}</span>{label}
            </a>
          ))}
        </nav>
        <div className="side-foot muted small">uv run studio web</div>
      </aside>
      <main className="main">
        <JobBar />
        <div className="content">{body}</div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Providers><App /></Providers>);
