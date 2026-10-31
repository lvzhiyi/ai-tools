"""流水线：分镜表 → 出图 → 配音 → 字幕 → 预览视频。

每一步的产物都会落盘，再次运行时已有的文件会跳过（加 --force 重做）。
所以想重画某个镜头，删掉 output/<项目>/images/03.png 再运行一次即可。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import storyboard as sbmod
from . import subtitle, video
from .comfy import ComfyClient
from .config import ROOT

STAGES = ("images", "audio", "subtitle", "video")


def _slug(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("_") or "project"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def project_dir(sb: dict) -> Path:
    return ROOT / "output" / _slug(sb["title"])


def run(sb_path: Path, cfg: dict, only: str | None = None, force: bool = False, command: str = "") -> Path:
    from .metrics import Metrics

    sb = sbmod.load(sb_path)
    out = project_dir(sb)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    shutil.copy(sb_path, out / "storyboard.json")
    _log(f"项目「{sb['title']}」，共 {len(sb['shots'])} 个镜头 → {out}")
    m = Metrics(sb["title"], out, cfg, command=command)
    status, error = "ok", None
    try:
        _run(sb, out, cfg, only, force, m)
    except BaseException as e:
        status, error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        run_dir = m.finish(status, error)
        _log(f"运行报告：{(run_dir / 'report.md').relative_to(ROOT)}（总耗时 {m.summary()['total_sec']:.0f}s）")
    _log(f"全部完成 ✅  打开文件夹：open \"{out}\"")
    return out


def _run(sb: dict, out: Path, cfg: dict, only: str | None, force: bool, m) -> None:
    stages = [only] if only else list(STAGES)
    if "images" in stages:
        with m.stage("images"):
            _images(sb, out, cfg, force, m, free_after=len(stages) > 1)
    if "audio" in stages:
        with m.stage("audio"):
            _audio(sb, out, cfg, force, m)
    timeline = _timeline(sb["shots"], out, cfg["video"])  # 字幕和视频都要用
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    if "subtitle" in stages:
        with m.stage("subtitle"):
            _subtitle(timeline, out, cfg)
    if "video" in stages:
        with m.stage("video"):
            _video(timeline, out, cfg)


def _images(sb: dict, out: Path, cfg: dict, force: bool, m, free_after: bool) -> None:
    """出图（带 AI 质检：不合格自动重画）。"""
    from .comfy import make_editor
    from .qa import produce_images

    comfy = ComfyClient(cfg["comfy"])
    comfy.ping()
    editor = make_editor(cfg["comfy"])
    _log("有角色的镜头：" + ("参考图编辑（照着定妆照画）" if editor else "文生图"))
    produce_images(sb, out, cfg, comfy, force=force, editor=editor, metrics=m)
    if free_after:
        _log("释放 ComfyUI 内存，给配音模型腾地方")
        comfy.free()


def _audio(sb: dict, out: Path, cfg: dict, force: bool, m) -> None:
    from .tts import Voice

    voice = Voice(cfg["tts"])
    todo = [s for s in sb["shots"] if s.get("line") and
            (force or not (out / "audio" / f"{sbmod.shot_name(s)}.wav").exists())]
    _log(f"配音：{len(todo)} 句待生成")
    for i, s in enumerate(todo, 1):
        t0 = time.time()
        path = out / "audio" / f"{sbmod.shot_name(s)}.wav"
        voice.say(s["line"], path, voice=s.get("voice"), instruct=s.get("emotion"))
        sec = time.time() - t0
        m.event("tts", shot=sbmod.shot_name(s), voice=s.get("voice") or "默认", chars=len(s["line"]),
                sec=round(sec, 2), audio_sec=round(_duration(path), 2))
        _log(f"  [{i}/{len(todo)}] 镜头 {sbmod.shot_name(s)}（{s.get('voice') or '默认'}）完成，用时 {sec:.0f}s")


def _subtitle(timeline: list[dict], out: Path, cfg: dict) -> None:
    lines = [(t["start"], t["start"] + t["speech"], t["line"]) for t in timeline if t["line"] and t["speech"]]
    srt = subtitle.build_srt(lines, cfg["subtitle"].get("max_chars", 16))
    (out / "subtitles.srt").write_text(srt, encoding="utf-8")
    _log("字幕：subtitles.srt 已生成")


def _video(timeline: list[dict], out: Path, cfg: dict) -> None:
    if not video.has_ffmpeg():
        _log("没找到 ffmpeg，跳过预览视频（brew install ffmpeg）")
        return
    clips_dir = out / "_clips"
    clips_dir.mkdir(exist_ok=True)
    clips = []
    for t in timeline:
        img = out / "images" / f"{t['name']}.png"
        if not img.exists():
            _log(f"  镜头 {t['name']} 没有图片，跳过")
            continue
        aud = out / "audio" / f"{t['name']}.wav"
        clips.append(video.make_clip(img, aud if aud.exists() else None, t["duration"],
                                     clips_dir / f"{t['name']}.mp4", cfg["video"]))
    if clips:
        video.concat(clips, out / "subtitles.srt", out / "preview.mp4")
        _log("预览视频：preview.mp4 已生成（字幕是软字幕，播放器里可开关）")
    shutil.rmtree(clips_dir, ignore_errors=True)


def _duration(path: Path) -> float:
    from .tts import duration
    try:
        return duration(path)
    except Exception:
        return 0.0


def _timeline(shots: list[dict], out: Path, vcfg: dict) -> list[dict]:
    from .tts import duration

    pad = float(vcfg.get("pad_sec", 0.4))
    silent = float(vcfg.get("silent_shot_sec", 2.0))
    t, rows = 0.0, []
    for s in shots:
        name = sbmod.shot_name(s)
        aud = out / "audio" / f"{name}.wav"
        speech = duration(aud) if aud.exists() else 0.0
        dur = float(s.get("duration") or (speech + pad if speech else silent))
        rows.append({"name": name, "start": round(t, 3), "duration": round(dur, 3),
                     "speech": round(speech, 3), "line": s.get("line", "")})
        t += dur
    return rows
