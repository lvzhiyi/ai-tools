"""收集"某个角色的所有图"：标注（label）和校准（calibrate）共用。

一个角色的图包括：
- 定妆照：分镜表里 ref 指定的图，或 output/<项目>/cast/<角色>.png；以及同目录下的左右侧脸 <名字>_left/right.png
- 镜头图：包含该角色的镜头的最终图 images/NN.png，和候选图 images/_candidates/NN_*.png（按内容去重）
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import storyboard as sbmod
from .config import ROOT


def fingerprint(path: Path) -> str:
    """文件内容指纹。标注记录这个值：同名文件被重画后指纹会变，旧标注自动失效。"""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def rel(path: Path) -> str:
    """相对项目根目录的路径，作为标注和报告里的统一写法。"""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def front_ref(sb: dict, out: Path, cid: str) -> Path:
    c = sb["characters"][cid]
    if c.get("ref"):
        p = Path(c["ref"]).expanduser()
        return p if p.is_absolute() else ROOT / p
    return out / "cast" / f"{cid}.png"


def reference_images(sb: dict, out: Path, cid: str) -> list[Path]:
    front = front_ref(sb, out, cid)
    if not front.exists():
        return []
    angles = [front.with_name(f"{front.stem}_{k}{front.suffix}") for k in ("left", "right")]
    return [front] + [a for a in angles if a.exists()]


def character_images(sb: dict, out: Path) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    img_dir = out / "images"
    cand = img_dir / "_candidates"
    for cid in sb["characters"]:
        imgs = reference_images(sb, out, cid)
        for s in sb["shots"]:
            if cid in s["characters"]:
                name = sbmod.shot_name(s)
                final = img_dir / f"{name}.png"
                imgs += ([final] if final.exists() else []) + sorted(cand.glob(f"{name}_*.png"))
        seen, uniq = set(), []
        for p in imgs:  # 最终图通常就是某张候选图的拷贝，按内容去重
            fp = fingerprint(p)
            if fp not in seen:
                seen.add(fp)
                uniq.append(p)
        if uniq:
            groups[cid] = uniq
    return groups
