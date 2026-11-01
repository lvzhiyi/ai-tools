"""环境检查：studio check 和网页控制台共用。

每一项返回 {"name", "status": ok / warn / fail, "detail"}：
- fail：流水线必需的东西坏了（ComfyUI 连不上、人脸模型没有……）
- warn：可选组件不可用（LM Studio 没开、图生视频引擎没装），只影响部分功能
"""
from __future__ import annotations

import json
import urllib.request


def _item(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def run(cfg: dict) -> list[dict]:
    from .comfy import ComfyClient, ComfyError, make_editor
    from .video import has_ffmpeg

    items: list[dict] = []
    try:
        c = ComfyClient(cfg["comfy"])
        stats = c.ping()
        dev = (stats or {}).get("devices", [{}])[0].get("type", "?")
        items.append(_item("ComfyUI", "ok", f"已连接（{cfg['comfy']['url']}，设备 {dev}）"))
        probs = c.check_workflow()
        items.append(_item("文生图工作流", "fail" if probs else "ok",
                           "；".join(probs) if probs else f"{c.profile}（{c.cfg['workflow']}）"))
        ed = make_editor(cfg["comfy"])
        if ed:
            probs = ed.check_workflow()
            items.append(_item("参考图编辑工作流", "fail" if probs else "ok",
                               "；".join(probs) if probs else ed.cfg["workflow"]))
        else:
            items.append(_item("参考图编辑工作流", "warn", "没有启用（config.toml 的 [comfy.edit]）"))
    except ComfyError as e:
        items.append(_item("ComfyUI", "fail", str(e)))

    try:
        with urllib.request.urlopen(cfg["lmstudio"]["url"].rstrip("/") + "/models", timeout=3) as r:
            models = [m["id"] for m in json.load(r).get("data", [])]
        items.append(_item("LM Studio", "ok", f"已连接，可用模型：{', '.join(models) or '（无）'}"))
    except Exception:
        items.append(_item("LM Studio", "warn", "没开（拆分镜和看图质检需要它）"))

    items.append(_item("ffmpeg", "ok", "已安装") if has_ffmpeg() else _item("ffmpeg", "fail", "没有 ffmpeg：brew install ffmpeg"))

    from .i2v import make_engine
    icfg = cfg.get("i2v", {})
    try:
        probs = make_engine(icfg).check()
    except Exception as e:
        probs = [str(e)]
    if probs:
        items.append(_item("图生视频引擎", "warn", "不可用（只有图生视频需要）：" + "；".join(probs)))
    else:
        items.append(_item("图生视频引擎", "ok", f"{icfg.get('engine_dir')}（{icfg.get('model')}）"
                           + ("" if icfg.get("enabled") else "，run 默认不跑，加 --i2v 开启")))

    try:
        from .face import FaceMatcher
        FaceMatcher()
        items.append(_item("人脸比对模型", "ok", "OpenCV YuNet + SFace"))
    except Exception as e:
        items.append(_item("人脸比对模型", "fail", str(e)))

    try:
        import mlx_audio  # noqa: F401
        items.append(_item("配音（mlx-audio）", "ok", "已安装"))
    except ImportError:
        items.append(_item("配音（mlx-audio）", "fail", "没装好：在项目目录运行 uv sync"))
    return items
