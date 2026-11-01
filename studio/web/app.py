"""网页控制台的后端（Starlette）：uv run studio web → http://127.0.0.1:8780

接口一览（都在 /api 下）：
  GET  /overview                     环境检查 + 当前任务 + 最近运行
  GET  /checks                       环境检查（重新检测）
  GET  /storyboards                  分镜表列表（含每个项目的完成度）
  POST /storyboards                  新建空白分镜表 {file, title}
  GET  /storyboards/{file}           读分镜表原文
  PUT  /storyboards/{file}           保存（先按命令行同一套规则校验）
  GET  /projects/{file}              项目详情：每个镜头的图、片段、配音、质检分数，定妆照，运行记录
  GET  /projects/{file}/runs/{id}    某次运行的报告（Markdown）
  GET  /projects/{file}/subtitles.vtt
  POST /projects/{file}/redo         重做一个镜头的图 / 配音 / 视频片段 {kind, shot}
  GET  /projects/{file}/label        标注用的图片对     POST 同路径：记一条标注 {key, value}
  GET  /scripts  POST /scripts  GET /scripts/{file}    剧本
  GET  /calibrations                 校准报告
  GET  /history                      所有运行的历史（runs.csv）
  GET  /config                       当前配置（只读，无效密钥环境变量名隐藏）
  GET/PUT /model-routing             网页模型模式（local / cloud / auto）
  GET  /jobs  POST /jobs  GET /jobs/{id}  POST /jobs/{id}/cancel
  GET  /jobs/{id}/events             SSE：任务日志和状态，实时推送
静态文件：/files/... → output/ 下的图、视频、音频；/ → 前端页面（studio/web/static）
"""
from __future__ import annotations

import asyncio
import copy
import re
import json
import random
import threading
import time
import webbrowser
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import checks as checks_mod
from ..config import ROOT, load_config
from . import projects as P
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
jobs: JobManager | None = None
_checks_cache: dict = {"at": 0.0, "items": []}


def manager() -> JobManager:
    global jobs
    if jobs is None:
        jobs = JobManager()
    return jobs


# ------------------------------------------------------------------ 通用
def ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def handle(fn):
    """统一错误格式：{"error": "..."}，页面直接把这句话显示给用户。"""
    async def wrapper(request: Request):
        try:
            return await fn(request)
        except P.NotFound as e:
            return ok({"error": str(e)}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return ok({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return ok({"error": f"{type(e).__name__}: {e}"}, 500)
    return wrapper


def _checks(refresh: bool = False) -> list[dict]:
    if refresh or time.time() - _checks_cache["at"] > 30:  # ComfyUI / LM Studio 探测要几秒，缓存 30 秒
        _checks_cache.update(at=time.time(), items=checks_mod.run(load_config()))
    return _checks_cache["items"]


# ------------------------------------------------------------------ 总览 / 配置
@handle
async def overview(request: Request):
    items = await asyncio.to_thread(_checks)
    hist = P.history()[-10:][::-1]
    return ok({"checks": items, "jobs": manager().active(), "recent_jobs": manager().list(8),
               "history": hist, "storyboards": len(list(P.SB_DIR.glob("*.json")))})


@handle
async def get_checks(request: Request):
    return ok(await asyncio.to_thread(_checks, True))


@handle
async def get_config(request: Request):
    cfg = copy.deepcopy(load_config())
    cloud = cfg.get("lmstudio", {}).get("cloud", {})
    key_env = cloud.get("api_key_env", "")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        cloud["api_key_env"] = "（无效环境变量名，已隐藏）"
    return ok(cfg)


@handle
async def model_routing(request: Request):
    from . import model_routing as routing
    if request.method == "PUT":
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求必须包含模型模式 mode")
        routing.set_mode(body.get("mode"))
    return ok(routing.status())


@handle
async def get_history(request: Request):
    return ok(P.history(request.query_params.get("project")))


# ------------------------------------------------------------------ 分镜表
@handle
async def storyboards(request: Request):
    if request.method == "POST":
        body = await request.json()
        return ok(P.new_storyboard(body["file"], body.get("title", "")), 201)
    return ok(P.list_storyboards())


@handle
async def storyboard(request: Request):
    file = request.path_params["file"]
    if request.method == "PUT":
        body = await request.json()
        return ok(P.write_storyboard(file, body["data"]))
    return ok(P.read_storyboard(file))


# ------------------------------------------------------------------ 项目
@handle
async def project(request: Request):
    return ok(await asyncio.to_thread(P.project, request.path_params["file"]))


@handle
async def run_report(request: Request):
    return PlainTextResponse(P.run_report(request.path_params["file"], request.path_params["run"]))


@handle
async def subtitles(request: Request):
    return Response(P.subtitles_vtt(request.path_params["file"]), media_type="text/vtt; charset=utf-8")


@handle
async def redo(request: Request):
    """重做一个镜头：旧文件挪进 _history，再排一个只补缺的任务。"""
    file = request.path_params["file"]
    body = await request.json()
    kind, shot = body["kind"], str(body["shot"])
    sb = f"storyboards/{P.sb_path(file).name}"
    if kind == "image":
        moved = P.archive(file, "image", shot)
        job = manager().submit("run", ["run", sb, "--only", "images"], f"{_title(file)} · 重画镜头 {shot}", file)
    elif kind == "audio":
        moved = P.archive(file, "audio", shot)
        job = manager().submit("run", ["run", sb, "--only", "audio"], f"{_title(file)} · 重新配音镜头 {shot}", file)
    elif kind == "clip":
        moved, seed = None, random.randint(1, 2**31 - 1)  # 同一张图、同一段描述，只有换种子才会出不同的动作
        job = manager().submit("i2v", ["i2v", sb, "--shots", shot, "--force", "--seed", str(seed)],
                               f"{_title(file)} · 重做视频片段 {shot}（seed {seed}）", file)
    else:
        raise ValueError(f"不支持的重做类型：{kind}")
    return ok({"job": job.public(), "archived": moved}, 201)


def _title(file: str) -> str:
    try:
        return P.read_storyboard(file)["data"].get("title") or file
    except Exception:
        return file


@handle
async def label(request: Request):
    file = request.path_params["file"]
    if request.method == "POST":
        body = await request.json()
        P.save_label(file, body["key"], body["value"])
        return ok({"saved": True})
    return ok(await asyncio.to_thread(P.label_data, file))


# ------------------------------------------------------------------ 剧本 / 校准
@handle
async def scripts(request: Request):
    if request.method == "POST":
        body = await request.json()
        p = P.save_script(body["file"], body["text"])
        return ok({"file": p.name}, 201)
    return ok(P.list_scripts())


@handle
async def script(request: Request):
    return PlainTextResponse(P.read_script(request.path_params["file"]))


@handle
async def calibrations(request: Request):
    return ok(P.calibrations())


# ------------------------------------------------------------------ 任务
def build_job(body: dict) -> tuple[str, list[str], str, str | None]:
    """页面传来的是"做什么 + 选项"，命令行参数在这里按白名单拼，不接受任意参数。"""
    kind = body["kind"]
    o = body.get("options") or {}
    file = body.get("file")
    sb = f"storyboards/{P.sb_path(file).name}" if file else None
    title = _title(file) if file else ""
    names = {"images": "出图", "audio": "配音", "i2v": "图生视频", "subtitle": "字幕", "video": "预览视频"}
    if kind == "run":
        argv = ["run", sb]
        if o.get("only"):
            if o["only"] not in names:
                raise ValueError(f"未知步骤：{o['only']}")
            argv += ["--only", o["only"]]
        argv += [f for k, f in (("force", "--force"), ("no_qa", "--no-qa"), ("no_edit", "--no-edit"),
                                ("i2v", "--i2v")) if o.get(k)]
        what = names.get(o.get("only"), "全流程" + ("（含图生视频）" if o.get("i2v") else ""))
        return kind, argv, f"{title} · {what}", file
    if kind == "i2v":
        argv = ["i2v", sb]
        if o.get("shots"):
            argv += ["--shots", ",".join(str(x) for x in o["shots"])]
        if o.get("force"):
            argv.append("--force")
        if o.get("seed"):
            argv += ["--seed", str(int(o["seed"]))]
        shots = f"（镜头 {','.join(map(str, o['shots']))}）" if o.get("shots") else ""
        return kind, argv, f"{title} · 图生视频{shots}", file
    if kind == "cast":
        return kind, ["cast", sb] + (["--force"] if o.get("force") else []), f"{title} · 定妆照", file
    if kind == "qa":
        return kind, ["qa", sb] + (["--no-vision"] if o.get("no_vision") else []), f"{title} · 质检", file
    if kind == "calibrate":
        return kind, ["calibrate", sb, "--people", str(int(o.get("people", 6)))], f"{title} · 阈值校准", file
    if kind == "plan":
        script_file = P.safe_name(o["script"], ".txt")
        if not (P.SCRIPT_DIR / script_file).exists():
            raise P.NotFound(f"剧本不存在：{script_file}")
        return kind, ["plan", f"screenplays/{script_file}", "--max-shots", str(int(o.get("max_shots", 12)))], \
            f"拆分镜 · {script_file}", None
    if kind == "image":
        argv = ["image", str(o["prompt"])] + (["--seed", str(int(o["seed"]))] if o.get("seed") else [])
        return kind, argv, "试一张图", None
    raise ValueError(f"未知任务类型：{kind}")


@handle
async def jobs_root(request: Request):
    if request.method == "POST":
        kind, argv, title, file = build_job(await request.json())
        return ok(manager().submit(kind, argv, title, file).public(), 201)
    return ok(manager().list(int(request.query_params.get("limit", 50))))


@handle
async def job_detail(request: Request):
    try:
        job = manager().get(request.path_params["id"])
    except KeyError:
        raise P.NotFound("任务不存在")
    return ok(job.public(with_lines=True))


@handle
async def job_cancel(request: Request):
    try:
        return ok(manager().cancel(request.path_params["id"]).public())
    except KeyError:
        raise P.NotFound("任务不存在")


async def job_events(request: Request):
    """SSE：先补发已有的日志，然后每 0.3 秒推新行；任务结束发 done 事件后关闭。

    用轮询而不是回调：日志在 worker 线程里写，这里只按下标读，不需要跨线程通知，
    断线重连时带上 ?from=N 就能从第 N 行接着收。
    """
    try:
        job = manager().get(request.path_params["id"])
    except KeyError:
        return ok({"error": "任务不存在"}, 404)
    start = int(request.query_params.get("from", 0))

    async def stream():
        sent = start
        last_state = None
        while True:
            if await request.is_disconnected():
                break
            lines = job.lines
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"event: line\ndata: {json.dumps(line, ensure_ascii=False)}\n\n"
                sent = len(lines)
            state = (job.status, tuple(job.progress or ()), job.stage, len(lines))
            if state != last_state:
                last_state = state
                yield f"event: status\ndata: {json.dumps(job.public(), ensure_ascii=False)}\n\n"
            if job.status in ("succeeded", "failed", "cancelled") and sent >= len(job.lines):
                yield f"event: done\ndata: {json.dumps(job.public(), ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ 页面
async def index(request: Request):
    page = STATIC / "index.html"
    if not page.exists():
        return PlainTextResponse("前端还没构建：cd web && npm install && npm run build", status_code=500)
    return FileResponse(page, headers={"Cache-Control": "no-cache"})


def create_app() -> Starlette:
    (ROOT / "output").mkdir(exist_ok=True)
    api = [
        Route("/overview", overview), Route("/checks", get_checks), Route("/config", get_config),
        Route("/model-routing", model_routing, methods=["GET", "PUT"]),
        Route("/history", get_history),
        Route("/storyboards", storyboards, methods=["GET", "POST"]),
        Route("/storyboards/{file}", storyboard, methods=["GET", "PUT"]),
        Route("/projects/{file}", project),
        Route("/projects/{file}/runs/{run}", run_report),
        Route("/projects/{file}/subtitles.vtt", subtitles),
        Route("/projects/{file}/redo", redo, methods=["POST"]),
        Route("/projects/{file}/label", label, methods=["GET", "POST"]),
        Route("/scripts", scripts, methods=["GET", "POST"]), Route("/scripts/{file}", script),
        Route("/calibrations", calibrations),
        Route("/jobs", jobs_root, methods=["GET", "POST"]),
        Route("/jobs/{id}", job_detail), Route("/jobs/{id}/cancel", job_cancel, methods=["POST"]),
        Route("/jobs/{id}/events", job_events),
    ]
    routes = [
        Mount("/api", routes=api),
        Mount("/files", app=StaticFiles(directory=ROOT / "output"), name="files"),
        Route("/", index),
    ]
    if STATIC.exists():
        routes.append(Mount("/static", app=StaticFiles(directory=STATIC), name="static"))
    return Starlette(routes=routes)


def serve(host: str = "127.0.0.1", port: int = 8780, open_browser: bool = True) -> None:
    import uvicorn

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"网页控制台：{url}（Ctrl+C 结束；正在跑的任务会一起停止）")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
