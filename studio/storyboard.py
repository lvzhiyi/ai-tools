"""分镜表的读取和校验。

格式（JSON）：
{
  "title": "第一集",
  "defaults": {"voice": "Vivian"},
  "characters": {
    "linxia": {"name": "林夏", "look": "a young East Asian woman with ..., wearing ...", "ref": "可选：定妆照路径"}
  },
  "shots": [
    {"id": 2, "image": "Cinematic ... {linxia} looks back ...", "characters": ["linxia"],
     "line": "台词", "voice": "Vivian", "emotion": "紧张", "seed": 123, "duration": 2.5,
     "motion": "{linxia} slowly turns her head, camera pushes in"}
  ]
}
image 必填，其余选填。motion 是图生视频用的动作描述（英文，也可以用 {角色id}）。image 里的 {角色id} 会被替换成该角色的 look，保证每个镜头外貌描述一致；
写了 {角色id} 的角色会自动加进 characters（用于人脸一致性质检）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w-]*)\}")


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        sb = json.load(f)
    if isinstance(sb, list):
        sb = {"shots": sb}
    sb.setdefault("title", path.stem)
    sb.setdefault("defaults", {})
    chars = sb.get("characters") or {}
    if isinstance(chars, list):  # 也接受数组格式
        chars = {c["id"]: c for c in chars}
    sb["characters"] = chars
    for cid, c in chars.items():
        if not c.get("look"):
            raise ValueError(f"角色 {cid} 缺少 look（外貌描述）")

    shots = sb.get("shots") or []
    if not shots:
        raise ValueError(f"{path} 里没有镜头（shots 为空）")
    for i, s in enumerate(shots, 1):
        s.setdefault("id", i)
        if not s.get("image"):
            raise ValueError(f"第 {s['id']} 个镜头缺少 image（画面描述）")
        for k, v in sb["defaults"].items():
            s.setdefault(k, v)
        used = _PLACEHOLDER.findall(s["image"])
        unknown = [u for u in used if u not in chars]
        if unknown:
            raise ValueError(f"第 {s['id']} 个镜头用了未定义的角色：{unknown}")
        listed = list(s.get("characters") or [])
        for u in used:
            if u not in listed:
                listed.append(u)
        bad = [c for c in listed if c not in chars]
        if bad:
            raise ValueError(f"第 {s['id']} 个镜头的 characters 里有未定义的角色：{bad}")
        s["characters"] = listed
    return sb


def render_prompt(shot: dict, characters: dict, ref_char: str | None = None) -> str:
    """把 {角色id} 替换成外貌描述，得到最终送给出图模型的提示词。

    ref_char：编辑模式下作为参考图的角色。它的占位符换成"参考图里的人"，
    让模型照着参考图画脸，外貌描述只作补充。
    """
    def sub(m):
        cid = m.group(1)
        look = characters[cid]["look"]
        if cid == ref_char:
            return f"the same person from the reference image ({look})"
        return look
    return _PLACEHOLDER.sub(sub, shot["image"])


def shot_name(shot: dict) -> str:
    sid = shot["id"]
    return f"{sid:02d}" if isinstance(sid, int) else str(sid)
