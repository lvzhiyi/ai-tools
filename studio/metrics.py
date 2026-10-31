"""可观测性：每次运行自动记录耗时和关键指标。

产物：
- output/<项目>/runs/<时间>/metrics.json   原始数据（每个阶段、每张图、每句配音）
- output/<项目>/runs/<时间>/report.md      运行报告（阶段占比、最慢的步骤、和上一次对比）
- output/runs.csv                          所有项目的运行历史，一次一行，方便看趋势

用法：
    m = Metrics(project, out, cfg, command="run ...")
    with m.stage("images"):
        ...
        m.event("image", shot="02", mode="参考图", sec=54.2)
    m.finish()
"""
from __future__ import annotations

import csv
import json
import platform
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .config import ROOT

STAGE_NAMES = {"images": "出图 + 质检", "audio": "配音", "subtitle": "字幕", "video": "预览视频"}
HISTORY = ROOT / "output" / "runs.csv"
HISTORY_FIELDS = ["time", "project", "git", "status", "command", "t2i", "edit", "total_sec",
                  "images_sec", "audio_sec", "subtitle_sec", "video_sec", "images", "attempts",
                  "first_pass", "avg_image_sec", "tts_lines", "avg_tts_sec"]


def _git() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return (rev or "-") + ("+改动" if dirty else "")
    except Exception:
        return "-"


def _machine() -> str:
    mem = ""
    try:  # macOS：物理内存
        b = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3).stdout)
        mem = f" · {b / 2**30:.0f}GB"
    except Exception:
        pass
    return f"{platform.system()} {platform.machine()}{mem}"


class Metrics:
    def __init__(self, project: str, out: Path, cfg: dict, command: str = ""):
        self.project = project
        self.out = out
        self.started = time.time()
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        comfy = cfg.get("comfy", {})
        edit = comfy.get("edit")
        self.meta = {
            "project": project,
            "command": command,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "git": _git(),
            "machine": _machine(),
            "t2i": comfy.get("t2i_profile", "t2i"),
            "edit": bool(edit and edit.get("enabled", True)),
            "qa": cfg.get("qa", {}).get("enabled", True),
            "face_threshold": cfg.get("qa", {}).get("face_threshold"),
            "tts_model": cfg.get("tts", {}).get("model"),
        }
        self.stages: list[dict] = []
        self.events: list[dict] = []

    @contextmanager
    def stage(self, name: str):
        t0 = time.time()
        rec = {"name": name, "status": "ok"}
        try:
            yield rec
        except BaseException as e:
            rec["status"] = "failed"
            rec["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            rec["sec"] = round(time.time() - t0, 2)
            self.stages.append(rec)

    def event(self, kind: str, **data) -> None:
        self.events.append({"kind": kind, "t": round(time.time() - self.started, 2), **data})

    # ---------------------------------------------------------- 汇总
    def summary(self) -> dict:
        imgs = [e for e in self.events if e["kind"] == "image"]
        qa = [e for e in self.events if e["kind"] == "qa"]
        tts = [e for e in self.events if e["kind"] == "tts"]
        shots = {e["shot"] for e in imgs}
        first = [e for e in qa if e.get("round") == 1]
        by_mode: dict[str, list[float]] = {}
        for e in imgs:
            by_mode.setdefault(e.get("mode", "-"), []).append(e["sec"])
        audio_total = sum(e.get("audio_sec", 0) for e in tts)
        return {
            "total_sec": round(time.time() - self.started, 1),
            "stage_sec": {s["name"]: s["sec"] for s in self.stages},
            "images": len(shots),
            "attempts": len(imgs),
            "first_pass": f"{sum(e['passed'] for e in first)}/{len(first)}" if first else "-",
            "avg_image_sec": round(sum(e["sec"] for e in imgs) / len(imgs), 1) if imgs else None,
            "image_sec_by_mode": {k: round(sum(v) / len(v), 1) for k, v in by_mode.items()},
            "cast_sec": round(sum(e["sec"] for e in self.events if e["kind"].startswith("cast")), 1),
            "qa_sec": round(sum(e["sec"] for e in qa), 1),
            "tts_lines": len(tts),
            "avg_tts_sec": round(sum(e["sec"] for e in tts) / len(tts), 1) if tts else None,
            # RTF（实时率）= 生成耗时 / 音频时长，< 1 表示比实时还快
            "tts_rtf": round(sum(e["sec"] for e in tts) / audio_total, 2) if audio_total else None,
            "review": sorted({e["shot"] for e in qa if e.get("review")}),
        }

    def finish(self, status: str = "ok", error: str | None = None) -> Path:
        run_dir = self.out / "runs" / self.stamp
        n = 2
        while run_dir.exists():  # 同一秒内跑了两次
            run_dir = self.out / "runs" / f"{self.stamp}-{n}"
            n += 1
        run_dir.mkdir(parents=True)
        s = self.summary()
        data = {"meta": {**self.meta, "status": status, "error": error}, "summary": s,
                "stages": self.stages, "events": self.events}
        (run_dir / "metrics.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        prev = _previous(self.project, self.meta["command"])
        (run_dir / "report.md").write_text(_report(data, prev), encoding="utf-8")
        _append_history(self.meta, s, status)
        return run_dir


# ---------------------------------------------------------------- 历史
def _append_history(meta: dict, s: dict, status: str) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    new = not HISTORY.exists()
    st = s["stage_sec"]
    row = {
        "time": meta["started"], "project": meta["project"], "git": meta["git"], "status": status,
        "command": meta["command"], "t2i": meta["t2i"], "edit": meta["edit"], "total_sec": s["total_sec"],
        "images_sec": st.get("images", ""), "audio_sec": st.get("audio", ""), "subtitle_sec": st.get("subtitle", ""),
        "video_sec": st.get("video", ""), "images": s["images"], "attempts": s["attempts"],
        "first_pass": s["first_pass"], "avg_image_sec": s["avg_image_sec"] or "",
        "tts_lines": s["tts_lines"], "avg_tts_sec": s["avg_tts_sec"] or "",
    }
    with open(HISTORY, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def read_history(project: str | None = None) -> list[dict]:
    if not HISTORY.exists():
        return []
    with open(HISTORY, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if not project or r["project"] == project]


def _previous(project: str, command: str) -> dict | None:
    """同一项目、同一条命令的上一次成功运行 —— 只有跑的内容一样，耗时对比才有意义。"""
    rows = [r for r in read_history(project) if r["status"] == "ok" and r["command"] == command]
    return rows[-1] if rows else None


# ---------------------------------------------------------------- 报告
def _fmt(sec) -> str:
    if sec in (None, ""):
        return "-"
    sec = float(sec)
    return f"{sec / 60:.1f} 分钟" if sec >= 90 else f"{sec:.1f} 秒"


def _bar(frac: float, width: int = 20) -> str:
    n = round(frac * width)
    return "█" * n + "░" * (width - n)


def _report(data: dict, prev: dict | None) -> str:
    m, s = data["meta"], data["summary"]
    total = max(s["total_sec"], sum(st["sec"] for st in data["stages"])) or 1
    L = [f"# 运行报告 · {m['project']}", "",
         f"- 时间：{m['started']}　状态：{'✅ 成功' if m['status'] == 'ok' else '❌ 失败'}",
         f"- 命令：`{m['command']}`",
         f"- 版本：git {m['git']}　机器：{m['machine']}",
         f"- 模型：文生图 {m['t2i']}　参考图编辑 {'开' if m['edit'] else '关'}　质检 {'开' if m['qa'] else '关'}"
         f"（人脸阈值 {m['face_threshold']}）", ""]
    if m.get("error"):
        L += [f"> ❌ {m['error']}", ""]

    L += [f"## 总耗时 {_fmt(total)}", "", "| 阶段 | 耗时 | 占比 |", "|---|---|---|"]
    for st in data["stages"]:
        frac = st["sec"] / total
        L.append(f"| {STAGE_NAMES.get(st['name'], st['name'])}{'（失败）' if st['status'] != 'ok' else ''} "
                 f"| {_fmt(st['sec'])} | `{_bar(frac)}` {frac:.0%} |")

    if s["attempts"]:
        modes = "，".join(f"{k} {v} 秒" for k, v in s["image_sec_by_mode"].items())
        L += ["", "## 出图", "",
              f"- {s['images']} 个镜头共出图 {s['attempts']} 次（含重画），一次通过 {s['first_pass']}",
              f"- 平均每张：{modes}",
              f"- 定妆照：{_fmt(s['cast_sec'])}　质检：{_fmt(s['qa_sec'])}"]
        if s["review"]:
            L.append(f"- 👀 建议人工复核：{'、'.join(s['review'])}")
    if s["tts_lines"]:
        L += ["", "## 配音", "", f"- {s['tts_lines']} 句，平均每句 {s['avg_tts_sec']} 秒，"
              f"实时率 {s['tts_rtf']}（生成耗时 ÷ 音频时长，小于 1 就比实时快）"]

    slow = sorted([e for e in data["events"] if "sec" in e], key=lambda e: -e["sec"])[:5]
    if slow:
        L += ["", "## 最慢的 5 步", "", "| 步骤 | 镜头 | 耗时 |", "|---|---|---|"]
        for e in slow:
            what = {"image": f"出图（{e.get('mode', '')}，第 {e.get('round', 1)} 轮）", "qa": "质检",
                    "tts": f"配音（{e.get('voice', '')}）", "cast": "定妆照", "cast_angle": "侧脸定妆照"}.get(e["kind"], e["kind"])
            L.append(f"| {what} | {e.get('shot', e.get('character', '-'))} | {_fmt(e['sec'])} |")

    if not prev:
        L += ["", "## 和上一次对比", "", "这条命令第一次运行，下次再跑就能看到对比。"]
    else:
        L += ["", "## 和上一次对比（同一条命令）", "", f"上一次：{prev['time']}（git {prev['git']}，{prev['t2i']}）", "",
              "| 指标 | 上一次 | 这一次 | 变化 |", "|---|---|---|---|"]
        for key, label in [("total_sec", "总耗时"), ("images_sec", "出图 + 质检"), ("audio_sec", "配音"),
                           ("avg_image_sec", "平均每张图")]:
            now = s["total_sec"] if key == "total_sec" else (s["avg_image_sec"] if key == "avg_image_sec"
                                                             else s["stage_sec"].get(key.replace("_sec", "")))
            before = prev.get(key)
            if now in (None, "") or before in (None, ""):
                continue
            before, now = float(before), float(now)
            delta = (now - before) / before if before else 0
            arrow = "🟢 快了" if delta < -0.05 else ("🔴 慢了" if delta > 0.05 else "⚪ 持平")
            L.append(f"| {label} | {_fmt(before)} | {_fmt(now)} | {arrow} {abs(delta):.0%} |")
    return "\n".join(L) + "\n"


class _Null:
    """不记录指标时的占位对象，调用方不用到处判断 None。"""

    def event(self, *a, **k) -> None:
        pass


NULL = _Null()
