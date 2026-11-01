"""把分镜表和 output/<项目>/ 下散落的文件，整理成页面要的数据。

只读，不跑模型：网页打开一个项目时，所有图、片段、配音、质检分数都从这里一次拿到。
文件的 URL 带上修改时间（?v=），重画后浏览器不会显示缓存的旧图。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import storyboard as sbmod
from ..config import ROOT
from ..pipeline import project_dir

SB_DIR = ROOT / "storyboards"
SCRIPT_DIR = ROOT / "screenplays"
OUTPUT = ROOT / "output"
_SAFE = re.compile(r"^[\w\-\u4e00-\u9fff.]+$")


class NotFound(Exception):
    pass


def safe_name(name: str, suffix: str = ".json") -> str:
    """文件名只允许字母数字、中文、- _ .，防止 ../ 之类的路径穿越。"""
    name = name.strip()
    if not name.endswith(suffix):
        name += suffix
    if not _SAFE.match(name) or name.startswith("."):
        raise ValueError(f"文件名不合法：{name}（只能用字母、数字、中文、- _）")
    return name


def sb_path(file: str) -> Path:
    p = SB_DIR / safe_name(file)
    if not p.exists():
        raise NotFound(f"分镜表不存在：{file}")
    return p


def file_url(path: Path) -> str | None:
    """output/ 下的文件 → /files/... 的地址；不存在返回 None。"""
    if not path.exists():
        return None
    rel = path.resolve().relative_to(OUTPUT.resolve())
    return f"/files/{rel.as_posix()}?v={int(path.stat().st_mtime)}"


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ------------------------------------------------------------------ 列表
def list_storyboards() -> list[dict]:
    rows = []
    for p in sorted(SB_DIR.glob("*.json"), key=lambda x: -x.stat().st_mtime):
        try:
            sb = sbmod.load(p)
            err = None
        except Exception as e:  # 格式有问题的也列出来，方便在页面上修
            sb, err = {"title": p.stem, "shots": [], "characters": {}}, str(e)
        out = project_dir(sb)
        names = [sbmod.shot_name(s) for s in sb["shots"]]
        cover = next((out / "images" / f"{n}.png" for n in names if (out / "images" / f"{n}.png").exists()), None)
        rows.append({
            "file": p.name, "title": sb["title"], "error": err,
            "shots": len(names), "characters": len(sb["characters"]),
            "updated": p.stat().st_mtime,
            "done": {
                "images": sum((out / "images" / f"{n}.png").exists() for n in names),
                "audio": sum((out / "audio" / f"{n}.wav").exists() for n in names if _has_line(sb, n)),
                "lines": sum(1 for n in names if _has_line(sb, n)),
                "clips": sum((out / "clips" / f"{n}.mp4").exists() for n in names),
                "preview": (out / "preview.mp4").exists(),
            },
            "cover": file_url(cover) if cover else None,
        })
    return rows


def _has_line(sb: dict, name: str) -> bool:
    return any(sbmod.shot_name(s) == name and s.get("line") for s in sb["shots"])


# ------------------------------------------------------------------ 分镜表读写
def read_storyboard(file: str) -> dict:
    p = sb_path(file)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {"file": p.name, "data": raw}


def write_storyboard(file: str, data: dict, create: bool = False) -> dict:
    """先写临时文件，用和命令行一样的 storyboard.load 校验，通过了再替换。"""
    name = safe_name(file)
    p = SB_DIR / name
    if create and p.exists():
        raise ValueError(f"已经有同名分镜表：{name}")
    if not create and not p.exists():
        raise NotFound(f"分镜表不存在：{name}")
    tmp = SB_DIR / f".{name}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        sbmod.load(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(p)
    return read_storyboard(name)


def new_storyboard(file: str, title: str) -> dict:
    data = {
        "title": title or Path(file).stem,
        "defaults": {"voice": "Vivian"},
        "characters": {},
        "shots": [{"id": 1, "image": "Cinematic vertical film still of ...", "motion": "", "line": ""}],
    }
    return write_storyboard(file, data, create=True)


# ------------------------------------------------------------------ 项目详情
def project(file: str) -> dict:
    p = sb_path(file)
    sb = sbmod.load(p)
    out = project_dir(sb)
    qa = _json(out / "qa.json", {})
    vids = _i2v_results(out)
    timeline = {t["name"]: t for t in _json(out / "timeline.json", [])}

    shots = []
    for s in sb["shots"]:
        n = sbmod.shot_name(s)
        cands = sorted((out / "images" / "_candidates").glob(f"{n}_*.png"))
        q = qa.get(n) or {}
        final = q.get("final")
        attempts = q.get("attempts", [])
        chosen = next((a for a in attempts if Path(a.get("image", "")).name == final), attempts[-1] if attempts else None)
        shots.append({
            "name": n, "id": s["id"],
            "image_prompt": s["image"], "motion": s.get("motion", ""), "line": s.get("line", ""),
            "voice": s.get("voice"), "emotion": s.get("emotion"), "characters": s["characters"],
            "image": file_url(out / "images" / f"{n}.png"),
            "candidates": [{"name": c.name, "url": file_url(c)} for c in cands],
            "clip": file_url(out / "clips" / f"{n}.mp4"),
            "audio": file_url(out / "audio" / f"{n}.wav"),
            "timeline": timeline.get(n),
            "qa": _qa_view(chosen, len(attempts)) if chosen else None,
            "i2v": vids.get(n),
        })

    cast = []
    from ..samples import reference_images
    for cid, c in sb["characters"].items():
        refs = reference_images(sb, out, cid)
        cast.append({"id": cid, "name": c.get("name", cid), "look": c["look"],
                     "images": [{"label": _cast_label(r.stem, cid), "url": file_url(r)} for r in refs]})

    return {
        "file": p.name, "title": sb["title"], "dir": str(out.relative_to(ROOT)),
        "shots": shots, "cast": cast,
        "preview": file_url(out / "preview.mp4"),
        "subtitles": (f"/api/projects/{p.name}/subtitles.vtt" if (out / "subtitles.srt").exists() else None),
        "qa_report": (out / "qa_report.md").read_text(encoding="utf-8") if (out / "qa_report.md").exists() else None,
        "runs": runs(out),
    }


def _i2v_results(out: Path) -> dict:
    """视频片段的质检结果：优先读 clips/i2v.json；旧片段没有这个文件，就从运行记录里找最近一次。"""
    data = _json(out / "clips" / "i2v.json", {})
    for d in sorted((out / "runs").glob("*"), reverse=True):
        for e in (_json(d / "metrics.json", {}) or {}).get("events", []):
            if e.get("kind") == "i2v" and e.get("shot") not in data:
                data[e["shot"]] = {k: v for k, v in e.items() if k not in ("kind", "t", "shot")}
    return data


def _cast_label(stem: str, cid: str) -> str:
    return {"left": "左侧脸", "right": "右侧脸"}.get(stem.removeprefix(cid).lstrip("_"), "正脸")


def _qa_view(a: dict, attempts: int) -> dict:
    v = a.get("vision") or {}
    return {
        "passed": a.get("passed"), "attempts": attempts, "mode": a.get("mode"),
        "face": a.get("face") or {}, "face_ref": a.get("face_ref") or {},
        "match_score": v.get("match_score"), "summary": v.get("summary"),
        "defects": v.get("defects") or [], "extreme_expression": v.get("extreme_expression"),
        "notes": a.get("notes") or [], "gen_sec": a.get("gen_sec"),
    }


def runs(out: Path, limit: int = 30) -> list[dict]:
    rows = []
    for d in sorted((out / "runs").glob("*"), reverse=True)[:limit]:
        m = _json(d / "metrics.json", None)
        if not m:
            continue
        rows.append({"id": d.name, "status": m["meta"].get("status"), "command": m["meta"].get("command"),
                     "started": m["meta"].get("started"), "git": m["meta"].get("git"),
                     "total_sec": m["summary"].get("total_sec"), "stage_sec": m["summary"].get("stage_sec", {})})
    return rows


def run_report(file: str, run_id: str) -> str:
    out = project_dir(sbmod.load(sb_path(file)))
    if not re.match(r"^[\w\-]+$", run_id):
        raise NotFound(run_id)
    p = out / "runs" / run_id / "report.md"
    if not p.exists():
        raise NotFound(f"没有这次运行的报告：{run_id}")
    return p.read_text(encoding="utf-8")


def subtitles_vtt(file: str) -> str:
    """浏览器的 <track> 只认 WebVTT：SRT 的逗号毫秒改成点，加个文件头。"""
    out = project_dir(sbmod.load(sb_path(file)))
    srt = (out / "subtitles.srt").read_text(encoding="utf-8")
    body = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", srt)
    return "WEBVTT\n\n" + body


# ------------------------------------------------------------------ 重做某个镜头
def archive(file: str, kind: str, shot: str) -> str | None:
    """把要重做的文件挪到 _history/<时间>/，而不是删除：重画不满意还能找回来。"""
    sb = sbmod.load(sb_path(file))
    out = project_dir(sb)
    names = {sbmod.shot_name(s) for s in sb["shots"]}
    if shot not in names:
        raise NotFound(f"没有镜头 {shot}")
    src = {"image": out / "images" / f"{shot}.png", "audio": out / "audio" / f"{shot}.wav"}[kind]
    if not src.exists():
        return None
    dst = src.parent / "_history" / time.strftime("%Y%m%d-%H%M%S") / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    return str(dst.relative_to(ROOT))


# ------------------------------------------------------------------ 剧本
def list_scripts() -> list[dict]:
    SCRIPT_DIR.mkdir(exist_ok=True)
    return [{"file": p.name, "chars": len(p.read_text(encoding="utf-8")), "updated": p.stat().st_mtime,
             "storyboard": (SB_DIR / f"{p.stem}.json").exists()}
            for p in sorted(SCRIPT_DIR.glob("*.txt"), key=lambda x: -x.stat().st_mtime)]


def save_script(file: str, text: str) -> Path:
    SCRIPT_DIR.mkdir(exist_ok=True)
    p = SCRIPT_DIR / safe_name(file, ".txt")
    p.write_text(text, encoding="utf-8")
    return p


def read_script(file: str) -> str:
    p = SCRIPT_DIR / safe_name(file, ".txt")
    if not p.exists():
        raise NotFound(file)
    return p.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 标注 / 校准
def label_data(file: str) -> dict:
    from ..label import collect_pairs, load_labels

    sb = sbmod.load(sb_path(file))
    out = project_dir(sb)
    pairs = collect_pairs(sb, out)
    labels = load_labels(out)
    stored = load_labels(out, raw=True)
    for p in pairs:
        p["a_url"] = file_url(ROOT / p["a"])
        p["b_url"] = file_url(ROOT / p["b"])
    return {"pairs": pairs, "labels": labels, "stale": len(stored) - len(labels)}


def save_label(file: str, key: str, value: str) -> None:
    from ..label import collect_pairs, save_label as _save

    sb = sbmod.load(sb_path(file))
    out = project_dir(sb)
    pair = next((p for p in collect_pairs(sb, out) if p["key"] == key), None)
    if not pair:
        raise NotFound("这对图不存在（可能已经重画）")
    _save(out, pair, value)


def calibrations() -> list[dict]:
    rows = []
    for md in sorted((OUTPUT / "_calibrate").rglob("calibration.md")):
        d = md.parent
        result = _json(d / "calibration.json", {})
        rows.append({
            "profile": d.name if d.name != "_calibrate" else "（旧版）",
            "report": md.read_text(encoding="utf-8"),
            "svg": file_url(d / "distribution.svg"),
            "updated": md.stat().st_mtime,
            "summary": {k: result.get(k) for k in ("verdict", "suggested", "separable", "same", "diff", "compare")},
        })
    return sorted(rows, key=lambda r: -r["updated"])


def history(project_title: str | None = None) -> list[dict]:
    from ..metrics import read_history
    return read_history(project_title)
