"""分镜表的读取和校验。

格式（JSON）：
{
  "title": "第一集",
  "defaults": {"voice": "Vivian"},
  "shots": [
    {"id": 1, "image": "画面描述", "line": "台词", "voice": "Vivian", "emotion": "紧张", "seed": 123}
  ]
}
image 必填；line / voice / emotion / seed / duration 选填。
"""
from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        sb = json.load(f)
    if isinstance(sb, list):
        sb = {"shots": sb}
    sb.setdefault("title", path.stem)
    sb.setdefault("defaults", {})
    shots = sb.get("shots") or []
    if not shots:
        raise ValueError(f"{path} 里没有镜头（shots 为空）")
    for i, s in enumerate(shots, 1):
        s.setdefault("id", i)
        if not s.get("image"):
            raise ValueError(f"第 {s['id']} 个镜头缺少 image（画面描述）")
        for k, v in sb["defaults"].items():
            s.setdefault(k, v)
    return sb


def shot_name(shot: dict) -> str:
    sid = shot["id"]
    return f"{sid:02d}" if isinstance(sid, int) else str(sid)
