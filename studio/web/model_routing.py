"""网页模型模式设置；队列在提交时读取，不改用户的 config.toml。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from ..config import ROOT, load_config
from ..llm import cloud_status

MODES = {"local", "cloud", "auto"}
SETTINGS_FILE = ROOT / "output" / "_web" / "model-routing.json"
_lock = threading.Lock()


def get_mode() -> str:
    try:
        mode = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))["mode"]
        if mode in MODES:
            return mode
    except (OSError, ValueError, KeyError, TypeError):
        pass
    cfg = load_config()["lmstudio"]
    return "auto" if cfg.get("cloud", {}).get("enabled", False) else "local"


def set_mode(mode: str) -> None:
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("模型模式必须是 local、cloud 或 auto")
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_FILE.parent,
                                             delete=False) as f:
                temporary = Path(f.name)
                json.dump({"mode": mode}, f)
            os.replace(temporary, SETTINGS_FILE)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def status() -> dict:
    cfg = load_config()["lmstudio"]
    # 这里只检查能否显式选择云端，不进行网络请求。
    candidate = {**cfg, "cloud": {**cfg.get("cloud", {}), "enabled": True}}
    text_ready, text_detail = cloud_status(candidate)
    vision_ready, vision_detail = cloud_status(candidate, vision=True)
    return {"mode": get_mode(), "local_model": cfg.get("model", ""),
            "cloud_text": {"ready": text_ready, "detail": text_detail},
            "cloud_vision": {"ready": vision_ready, "detail": vision_detail}}
