"""MCP Server：让 Claude Desktop / Claude Code / 本地 Agent 直接操作 ai-tools。

运行方式（stdio，由 MCP 客户端拉起，不用手动开）：
    uv run python -m studio.mcp_server

设计取舍：
- 只做 `studio web` HTTP API 的薄客户端，不自己跑任务。
  24GB 统一内存一次只能跑一个大模型，任务队列必须只有一个主人（web 里的 JobManager）；
  MCP 提交的任务和网页上点的任务排在同一个队列里，网页上也能看到、能取消。
- 长任务异步：出图、图生视频动辄几分钟，超过 MCP 客户端的调用超时。
  start_job 立即返回任务 ID，wait_job 最多阻塞 50 秒并上报进度，没跑完就让模型再调一次。
- 只暴露白名单操作（和网页同一套 build_job 校验），不提供"执行任意命令"。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Context, Image, MCPServer  # mcp 2.x：FastMCP 改名为 MCPServer
from mcp.types import ToolAnnotations

logging.getLogger("httpx").setLevel(logging.WARNING)  # wait_job 每 2 秒轮询一次，别让每个请求都打 INFO 日志

BASE_URL = os.environ.get("STUDIO_URL", "http://127.0.0.1:8780")
WAIT_MAX = 50  # 秒：单次 wait_job 的上限，留余量给客户端默认 60 秒左右的超时
POLL = 2.0
DONE = {"succeeded", "failed", "cancelled"}

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
OVERWRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

mcp = MCPServer(
    "ai-tools",
    instructions=(
        "本地 AI 短剧流水线（分镜表 → 定妆照 → 出图 + 人脸质检 → 配音 → 图生视频 → 预览视频）。"
        "长任务是异步的：start_job / redo_shot 返回任务 ID 后，用 wait_job 等结果，"
        "wait_job 返回 running 就再调一次。一次只能跑一个任务，其余排队。"
        "改分镜表前先 get_storyboard 读原文，在原文上修改后整份 save_storyboard。"
    ),
)


class StudioError(RuntimeError):
    pass


def _client() -> httpx.Client:  # 测试里替换成 MockTransport
    return httpx.Client(base_url=BASE_URL, timeout=30)


def _api(method: str, path: str, **kw) -> Any:
    try:
        with _client() as c:
            r = c.request(method, path, **kw)
    except httpx.ConnectError:
        raise StudioError(f"连不上 {BASE_URL}：请先在 ai-tools 目录运行 `uv run studio web --no-browser`")
    except httpx.TimeoutException:
        raise StudioError(f"{BASE_URL}{path} 30 秒没响应，studio web 可能卡住了")
    if r.headers.get("content-type", "").startswith("application/json"):
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            raise StudioError(data["error"])
    else:
        data = r.text
    r.raise_for_status()
    return data


def _job_view(j: dict, tail: int = 20) -> dict:
    """给模型看的任务状态：去掉内部字段，日志只留最后几行，省 token。"""
    v = {k: j.get(k) for k in ("id", "title", "status", "stage", "progress", "returncode", "command")}
    if j.get("started") is not None:
        end = j["finished"] if j.get("finished") is not None else time.time()
        v["elapsed_sec"] = round(end - j["started"])
    if "lines" in j:
        v["log_tail"] = j["lines"][-tail:]
    return v


# ------------------------------------------------------------------ 只读
@mcp.tool(annotations=READ)
def check_env() -> list[dict]:
    """检查环境：ComfyUI、LM Studio、ffmpeg、配音模型是否可用。跑任务前先调一次。"""
    return _api("GET", "/api/checks")


@mcp.tool(annotations=READ)
def list_storyboards() -> list[dict]:
    """列出所有分镜表和完成度（出图、配音、视频片段、预览视频各完成了多少）。"""
    rows = _api("GET", "/api/storyboards")
    return [{k: r.get(k) for k in ("file", "title", "shots", "characters", "done", "error")} for r in rows]


@mcp.tool(annotations=READ)
def get_storyboard(file: str) -> dict:
    """读取分镜表原文（JSON）：title、characters（角色卡）、shots（镜头）。"""
    return _api("GET", f"/api/storyboards/{file}")["data"]


@mcp.tool(annotations=READ)
def get_project(file: str) -> dict:
    """项目结果：每个镜头有没有图 / 配音 / 视频片段，以及人脸质检分数；附质检报告。"""
    p = _api("GET", f"/api/projects/{file}")
    shots = [{
        "name": s["name"], "id": s["id"], "line": s.get("line"), "characters": s.get("characters"),
        "has_image": bool(s.get("image")), "has_audio": bool(s.get("audio")), "has_clip": bool(s.get("clip")),
        "qa": s.get("qa"),
    } for s in p["shots"]]
    return {"title": p["title"], "shots": shots, "has_preview": bool(p.get("preview")),
            "qa_report": p.get("qa_report"), "recent_runs": (p.get("runs") or [])[:3]}


@mcp.tool(annotations=READ)
def view_shot_image(file: str, shot: str, max_side: int = 512) -> Image:
    """看某个镜头的成图（shot 用 get_project 里的 name）。默认缩到 512px，够判断构图和人物。"""
    p = _api("GET", f"/api/projects/{file}")
    s = next((x for x in p["shots"] if x["name"] == shot or str(x["id"]) == str(shot)), None)
    if not s or not s.get("image"):
        raise StudioError(f"镜头 {shot} 还没有图")
    with _client() as c:
        raw = c.get(s["image"]).content
    return Image(data=_shrink(raw, max_side), format="jpeg")


def _shrink(png: bytes, max_side: int) -> bytes:
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise StudioError("图片读取失败（文件可能还在写入，稍后再试）")
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


# ------------------------------------------------------------------ 写入 / 任务
@mcp.tool(annotations=OVERWRITE)
def save_storyboard(file: str, data: dict, create: bool = False) -> dict:
    """整份保存分镜表（和命令行同一套校验，不合法会报错且不覆盖）。create=True 表示新建。"""
    if create:
        _api("POST", "/api/storyboards", json={"file": file, "title": data.get("title", "")})
    saved = _api("PUT", f"/api/storyboards/{file}", json={"data": data})
    return {"file": saved["file"], "shots": len(saved["data"].get("shots", []))}


@mcp.tool(annotations=WRITE)
def start_job(
    kind: Literal["run", "i2v", "cast", "qa", "plan", "image"],
    file: str | None = None,
    options: dict | None = None,
) -> dict:
    """提交任务，立即返回任务 ID（之后用 wait_job 等结果）。

    kind：run 全流程或单步 / i2v 图生视频 / cast 定妆照 / qa 只质检 / plan 剧本拆分镜 / image 试一张图
    options 示例：
      run   {"only": "images"|"audio"|"i2v"|"subtitle"|"video", "force": false, "i2v": false}
      i2v   {"shots": [2, 3], "force": true, "seed": 123}
      plan  {"script": "剧本.txt", "max_shots": 12}（file 留空）
      image {"prompt": "画面描述"}（file 留空）
    """
    j = _api("POST", "/api/jobs", json={"kind": kind, "file": file, "options": options or {}})
    return {**_job_view(j), "queued_ahead": _ahead_of(j)}


def _ahead_of(job: dict) -> int:
    """排在这个任务前面的任务数（正在跑的 + 比它早提交、还在排队的）。"""
    jobs = _api("GET", "/api/jobs", params={"limit": 200})
    return sum(1 for x in jobs if x["id"] != job["id"] and (
        x["status"] == "running" or (x["status"] == "queued" and x["created"] <= job.get("created", 0))))


@mcp.tool(annotations=WRITE)
def redo_shot(file: str, kind: Literal["image", "audio", "clip"], shot: str) -> dict:
    """重做一个镜头的图 / 配音 / 视频片段（旧文件自动归档到 _history），返回任务 ID。"""
    r = _api("POST", f"/api/projects/{file}/redo", json={"kind": kind, "shot": shot})
    return {**_job_view(r["job"]), "archived": r.get("archived"), "queued_ahead": _ahead_of(r["job"])}


@mcp.tool(annotations=READ)
def get_job(job_id: str, tail: int = 30) -> dict:
    """查任务状态和最后几行日志。"""
    return _job_view(_api("GET", f"/api/jobs/{job_id}"), tail)


@mcp.tool(annotations=READ)
async def wait_job(job_id: str, ctx: Context, timeout_sec: int = WAIT_MAX) -> dict:
    """等任务结束，最多等 timeout_sec 秒（上限 50），期间上报进度。返回 running 就再调一次。"""
    deadline = time.monotonic() + min(timeout_sec, WAIT_MAX)
    while True:
        j = await _to_thread(_api, "GET", f"/api/jobs/{job_id}")
        if j["status"] in DONE:
            return _job_view(j, tail=40 if j["status"] == "failed" else 10)
        cur, total = (j.get("progress") or [0, 0])
        if total:
            await ctx.report_progress(cur, total, message=f"{j.get('stage') or ''} {cur}/{total}".strip())
        if time.monotonic() >= deadline:
            return {**_job_view(j, tail=5), "hint": "还在跑，再调一次 wait_job"}
        await _sleep(POLL)


async def _to_thread(fn, *a):
    import anyio

    return await anyio.to_thread.run_sync(lambda: fn(*a))


async def _sleep(sec: float) -> None:
    import anyio

    await anyio.sleep(sec)


@mcp.tool(annotations=OVERWRITE)
def cancel_job(job_id: str) -> dict:
    """取消任务（排队中直接取消；运行中和 Ctrl+C 一样收尾，已生成的文件保留）。"""
    return _job_view(_api("POST", f"/api/jobs/{job_id}/cancel"))


def main() -> None:
    mcp.run()  # 默认 stdio


if __name__ == "__main__":
    main()
