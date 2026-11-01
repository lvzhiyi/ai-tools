"""任务队列：网页上点的每个操作，都变成一条 studio 命令，在子进程里排队执行。

为什么用子进程跑 CLI，而不是在 Web 服务里直接调函数：
- 复用：命令行和网页走同一条代码路径，命令行能跑的网页就能跑，报告、历史记录完全一样；
- 隔离：配音、出图这些步骤会把模型加载进进程内存，子进程结束就全部释放，Web 服务一直很轻；
- 可取消：发 SIGINT 给整个进程组，CLI 按 Ctrl+C 的逻辑收尾（已生成的文件保留、运行报告照常写）。

为什么一次只跑一个：24GB 统一内存放不下两个大模型同时工作，所以是单 worker 串行队列。

日志：子进程的输出逐行收进内存，并写到 output/_web/jobs/<id>.log；网页用 SSE 订阅。
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import ROOT

JOBS_DIR = ROOT / "output" / "_web" / "jobs"
MAX_LINES = 5000
_PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")
_STAGE_HINTS = [  # 流水线日志（"[时:分:秒] 出图：……"）以这些字开头时，更新"当前在做什么"
    ("定妆照", "定妆照"), ("出图", "出图 + 质检"), ("第 ", "出图 + 质检"), ("质检", "出图 + 质检"),
    ("配音", "配音"), ("图生视频", "图生视频"), ("字幕", "字幕"), ("预览视频", "预览视频"),
    ("对照组", "校准"), ("正样本", "校准"),
]
_TIMESTAMP = re.compile(r"^\[\d\d:\d\d:\d\d\]\s*")


@dataclass
class Job:
    id: str
    kind: str                 # run / i2v / cast / qa / plan / calibrate / image
    title: str                # 给人看的标题，比如「示例-雨夜 · 图生视频（镜头 2）」
    argv: list[str]
    project: str | None = None   # 分镜表文件名（storyboards/ 下），没有就是 None
    status: str = "queued"    # queued / running / succeeded / failed / cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    returncode: int | None = None
    progress: list[int] | None = None   # [当前, 总数]
    stage: str | None = None
    lines: list[str] = field(default_factory=list, repr=False)
    cancel_requested: bool = False

    def public(self, with_lines: bool = False) -> dict:
        d = asdict(self)
        d.pop("lines")
        d.pop("cancel_requested")
        d["command"] = "studio " + " ".join(self.argv)
        d["line_count"] = len(self.lines)
        if with_lines:
            d["lines"] = self.lines[-MAX_LINES:]
        return d


class JobManager:
    def __init__(self, python: str | None = None):
        self.python = python or sys.executable
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.q: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.current: str | None = None
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self._load_history()
        threading.Thread(target=self._worker, daemon=True, name="job-worker").start()

    # ---------------------------------------------------------------- 提交 / 取消
    def submit(self, kind: str, argv: list[str], title: str, project: str | None = None) -> Job:
        job = Job(id=time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:4], kind=kind, title=title,
                  argv=argv, project=project)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self._persist(job)
        self.q.put(job.id)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
            self._append(job, "（排队中被取消）")
            self._persist(job)
        elif job.status == "running" and self.proc and self.current == job_id:
            job.cancel_requested = True
            self._append(job, "⏹ 正在取消：已生成的文件会保留……")
            self._signal(signal.SIGINT)  # 和按 Ctrl+C 一样：CLI 会收尾、写运行报告
            threading.Timer(15, self._force_kill, args=(job_id,)).start()
        return job

    def _force_kill(self, job_id: str) -> None:
        if self.current == job_id and self.proc and self.proc.poll() is None:
            self._append(self.jobs[job_id], "⏹ 15 秒没退出，强制结束")
            self._signal(signal.SIGKILL)

    def _signal(self, sig: int) -> None:
        try:  # 整个进程组：图生视频引擎是 studio 的子进程，也要一起停
            os.killpg(os.getpgid(self.proc.pid), sig)
        except (ProcessLookupError, AttributeError):
            pass

    # ---------------------------------------------------------------- 查询
    def list(self, limit: int = 50) -> list[dict]:
        ids = self.order[-limit:][::-1]
        return [self.jobs[i].public() for i in ids]

    def get(self, job_id: str) -> Job:
        return self.jobs[job_id]

    def active(self) -> dict | None:
        """正在跑的任务 + 排队数，给页面顶部的状态条用。"""
        cur = self.jobs.get(self.current) if self.current else None
        queued = sum(1 for j in self.jobs.values() if j.status == "queued")
        return {"current": cur.public() if cur else None, "queued": queued}

    # ---------------------------------------------------------------- 执行
    def _worker(self) -> None:
        while True:
            job_id = self.q.get()
            job = self.jobs.get(job_id)
            if not job or job.status != "queued":
                continue
            self._execute(job)

    def _execute(self, job: Job) -> None:
        job.status, job.started = "running", time.time()
        self.current = job.id
        self._persist(job)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        cmd = [self.python, "-m", "studio.cli", *job.argv]
        self._append(job, "$ studio " + " ".join(job.argv))
        try:
            self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True, bufsize=0)
            buf = b""
            while True:  # 按字节读，\r（进度条刷新）和 \n 都算一行结束
                chunk = self.proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"\r\n|\n|\r", buf)
                buf = parts.pop()
                for p in parts:
                    self._append(job, p.decode("utf-8", "replace"))
            if buf:
                self._append(job, buf.decode("utf-8", "replace"))
            job.returncode = self.proc.wait()
        except Exception as e:  # 启动失败
            self._append(job, f"❌ 无法启动：{e}")
            job.returncode = -1
        job.status = "cancelled" if job.cancel_requested else ("succeeded" if job.returncode == 0 else "failed")
        job.finished = time.time()
        self.proc, self.current = None, None
        self._persist(job)

    def _append(self, job: Job, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        # 进度条（tqdm）每次刷新都是一行，只保留最新那一行，避免刷屏
        if job.lines and "%|" in line and "%|" in job.lines[-1]:
            job.lines[-1] = line
        else:
            job.lines.append(line)
            with open(JOBS_DIR / f"{job.id}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        if len(job.lines) > MAX_LINES:
            del job.lines[: len(job.lines) - MAX_LINES]
        ts = _TIMESTAMP.match(line)
        if ts:  # 只看带时间戳的流水线日志，而且只看开头：「预览视频：……字幕是软字幕」不能算成字幕阶段
            rest = line[ts.end():]
            for key, stage in _STAGE_HINTS:
                if rest.startswith(key):
                    if stage != job.stage:
                        job.progress = None  # 换了阶段，上一阶段的 [i/n] 不再适用
                    job.stage = stage
                    break
        m = _PROGRESS.search(line)
        if m:
            job.progress = [int(m.group(1)), int(m.group(2))]

    # ---------------------------------------------------------------- 持久化
    def _persist(self, job: Job) -> None:
        (JOBS_DIR / f"{job.id}.json").write_text(json.dumps(job.public(), ensure_ascii=False, indent=2),
                                                 encoding="utf-8")

    def _load_history(self, keep: int = 100) -> None:
        """服务重启后，之前的任务记录和日志还在（运行中被打断的标为失败）。"""
        files = sorted(JOBS_DIR.glob("*.json"))[-keep:]
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            log = JOBS_DIR / f"{d['id']}.log"
            job = Job(**{k: d.get(k) for k in ("id", "kind", "title", "argv", "project", "status", "created",
                                               "started", "finished", "returncode", "progress", "stage")})
            job.lines = log.read_text(encoding="utf-8").splitlines()[-MAX_LINES:] if log.exists() else []
            if job.status in ("queued", "running"):
                job.status = "failed"
                job.lines.append("（控制台重启，任务中断）")
            self.jobs[job.id] = job
            self.order.append(job.id)


def project_paths(path: Path) -> str:
    """把绝对路径转成 ROOT 相对路径（给命令行参数用，日志里更短）。"""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
