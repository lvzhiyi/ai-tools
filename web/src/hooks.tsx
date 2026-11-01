import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Job, type ModelMode, type ModelRouting } from "./api";

// ------------------------------------------------------------------ 数据加载
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const my = ++seq.current;
    setLoading(true);
    fn().then(
      (d) => { if (my === seq.current) { setData(d); setError(null); } },
      (e) => { if (my === seq.current) setError(String(e.message || e)); },
    ).finally(() => { if (my === seq.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, loading, reload, setData };
}

// ------------------------------------------------------------------ 路由（hash）
// #/p/example.json/shots → ["p", "example.json", "shots"]
export function useRoute(): string[] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
export const go = (...parts: string[]) => { window.location.hash = "/" + parts.map(encodeURIComponent).join("/"); };
export const href = (...parts: string[]) => "#/" + parts.map(encodeURIComponent).join("/");

// ------------------------------------------------------------------ 提示
type Toast = { id: number; text: string; kind: "ok" | "fail" | "info"; link?: string };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"], link?: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

// ------------------------------------------------------------------ 任务：全局轮询
// 顶部状态条和各页面都要知道"现在在跑什么、刚刚跑完什么"，这里每 2 秒拉一次任务列表；
// 任务从运行中变成结束时，通知订阅者（比如项目页自动刷新）并弹提示。
interface JobsState {
  jobs: Job[];
  submit: (kind: string, file: string | null, options?: Record<string, unknown>) => Promise<Job | null>;
  onFinish: (fn: (job: Job) => void) => () => void;
  refresh: () => void;
}
const JobsCtx = createContext<JobsState>({ jobs: [], submit: async () => null, onFinish: () => () => {}, refresh: () => {} });
export const useJobs = () => useContext(JobsCtx);

interface ModelRoutingState {
  data: ModelRouting | null;
  error: string | null;
  pending: ModelMode | null;
  loading: boolean;
  reload: () => void;
  choose: (mode: ModelMode) => Promise<void>;
}
const ModelRoutingCtx = createContext<ModelRoutingState>({ data: null, error: null, pending: null,
  loading: true, reload: () => {}, choose: async () => {} });
export const useModelRouting = () => useContext(ModelRoutingCtx);

export function Providers({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((text: string, kind: Toast["kind"] = "info", link?: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind, link }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "fail" ? 9000 : 5000);
  }, []);

  const routing = useAsync(() => api.modelRouting(), []);
  const [pendingMode, setPendingMode] = useState<ModelMode | null>(null);
  const [routingError, setRoutingError] = useState<string | null>(null);
  const chooseMode = async (mode: ModelMode) => {
    setPendingMode(mode);
    setRoutingError(null);
    try {
      const data = await api.setModelRouting(mode);
      routing.setData(data);
      toast("模型模式已保存，对新提交的任务生效", "ok");
    } catch (error) {
      setRoutingError((error as Error).message);
    } finally {
      setPendingMode(null);
    }
  };

  const [jobs, setJobs] = useState<Job[]>([]);
  const prev = useRef<Map<string, string>>(new Map());
  const listeners = useRef(new Set<(j: Job) => void>());
  const refresh = useCallback(() => {
    api.jobs().then((list) => {
      const before = prev.current;
      for (const j of list) {
        const was = before.get(j.id);
        if (was && (was === "running" || was === "queued") && ["succeeded", "failed", "cancelled"].includes(j.status)) {
          listeners.current.forEach((fn) => fn(j));
          const word = { succeeded: "完成 ✅", failed: "失败 ❌", cancelled: "已取消" }[j.status as "succeeded"];
          toast(`${j.title} ${word}`, j.status === "succeeded" ? "ok" : "fail", href("jobs", j.id));
        }
      }
      prev.current = new Map(list.map((j) => [j.id, j.status]));
      setJobs(list);
    }).catch(() => {});
  }, [toast]);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);

  const submit = useCallback(async (kind: string, file: string | null, options: Record<string, unknown> = {}) => {
    if (pendingMode || routing.loading || routing.error || routingError) {
      toast("模型模式正在保存或尚未加载，请稍后重试", "info");
      return null;
    }
    try {
      const job = await api.submit(kind, file, options);
      toast(`已加入队列：${job.title}`, "info", href("jobs", job.id));
      refresh();
      return job;
    } catch (e) {
      toast(String((e as Error).message), "fail");
      return null;
    }
  }, [refresh, toast, pendingMode, routing.loading, routing.error, routingError]);
  const onFinish = useCallback((fn: (j: Job) => void) => {
    listeners.current.add(fn);
    return () => { listeners.current.delete(fn); };
  }, []);

  return (
    <ToastCtx.Provider value={toast}>
      <JobsCtx.Provider value={{ jobs, submit, onFinish, refresh }}>
        <ModelRoutingCtx.Provider value={{ data: routing.data, error: routingError || routing.error,
          pending: pendingMode, loading: routing.loading, reload: () => { setRoutingError(null); routing.reload(); }, choose: chooseMode }}>
          {children}
        </ModelRoutingCtx.Provider>
        <div className="toasts">
          {toasts.map((t) => (
            <div key={t.id} className={`toast ${t.kind}`} onClick={() => t.link && (window.location.hash = t.link.slice(1))}>
              {t.text}{t.link ? <span className="muted"> · 查看</span> : null}
            </div>
          ))}
        </div>
      </JobsCtx.Provider>
    </ToastCtx.Provider>
  );
}

/** 某个项目的任务一结束就回调（用来刷新项目数据）。 */
export function useOnProjectJobFinished(file: string | null, fn: () => void) {
  const { onFinish } = useJobs();
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => onFinish((j) => { if (!file || j.project === file) ref.current(); }), [onFinish, file]);
}

// ------------------------------------------------------------------ 任务日志：SSE
export function useJobStream(id: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    if (!id) return;
    setLines([]);
    setJob(null);
    let es: EventSource | null = null;
    let received = 0;          // 服务端已发来的行数：断线重连时从这里接着收（?from=）
    let finished = false;
    let timer: number | null = null;
    let retry: number | null = null;
    const buf: string[] = [];
    const isBar = (l: string) => l.includes("%|");
    const flush = () => {
      timer = null;
      const batch = buf.splice(0);
      // 进度条（tqdm）每次刷新都是一行，连续的只保留最新一行；一批行攒 100ms 再渲染，日志多时不卡
      setLines((old) => {
        const out = old.slice();
        for (const l of batch) {
          if (isBar(l) && out.length && isBar(out[out.length - 1])) out[out.length - 1] = l;
          else out.push(l);
        }
        return out;
      });
    };
    const open = () => {
      es = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events?from=${received}`);
      es.addEventListener("line", (e) => {
        received++;
        buf.push(JSON.parse((e as MessageEvent).data) as string);
        if (timer === null) timer = window.setTimeout(flush, 100);
      });
      es.addEventListener("status", (e) => setJob(JSON.parse((e as MessageEvent).data)));
      es.addEventListener("done", (e) => {
        finished = true;
        setJob(JSON.parse((e as MessageEvent).data));
        es?.close();
      });
      es.onerror = () => {  // 断线（比如控制台重启）：关掉，1 秒后从断点重连
        es?.close();
        if (!finished) retry = window.setTimeout(open, 1000);
      };
    };
    open();
    return () => {
      finished = true;
      es?.close();
      if (timer !== null) clearTimeout(timer);
      if (retry !== null) clearTimeout(retry);
    };
  }, [id]);
  return { job, lines };
}
