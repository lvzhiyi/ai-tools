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


def run(sb_path: Path, cfg: dict, only: str | None = None, force: bool = False) -> Path:
    sb = sbmod.load(sb_path)
    out = ROOT / "output" / _slug(sb["title"])
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    shutil.copy(sb_path, out / "storyboard.json")
    shots = sb["shots"]
    stages = [only] if only else list(STAGES)
    _log(f"项目「{sb['title']}」，共 {len(shots)} 个镜头 → {out}")

    # 1. 出图
    if "images" in stages:
        comfy = ComfyClient(cfg["comfy"])
        comfy.ping()
        todo = [s for s in shots if force or not (out / "images" / f"{sbmod.shot_name(s)}.png").exists()]
        _log(f"出图：{len(todo)} 张待生成（已有 {len(shots) - len(todo)} 张跳过）")
        for i, s in enumerate(todo, 1):
            t0 = time.time()
            path = out / "images" / f"{sbmod.shot_name(s)}.png"
            comfy.generate(s["image"], path, seed=s.get("seed"))
            _log(f"  [{i}/{len(todo)}] 镜头 {sbmod.shot_name(s)} 完成，用时 {time.time() - t0:.0f}s")
        if todo and len(stages) > 1:
            _log("释放 ComfyUI 内存，给配音模型腾地方")
            comfy.free()

    # 2. 配音
    if "audio" in stages:
        from .tts import Voice
        voice = Voice(cfg["tts"])
        todo = [s for s in shots if s.get("line") and
                (force or not (out / "audio" / f"{sbmod.shot_name(s)}.wav").exists())]
        _log(f"配音：{len(todo)} 句待生成")
        for i, s in enumerate(todo, 1):
            t0 = time.time()
            path = out / "audio" / f"{sbmod.shot_name(s)}.wav"
            voice.say(s["line"], path, voice=s.get("voice"), instruct=s.get("emotion"))
            _log(f"  [{i}/{len(todo)}] 镜头 {sbmod.shot_name(s)}（{s.get('voice') or '默认'}）完成，用时 {time.time() - t0:.0f}s")

    # 3. 时间轴（字幕和视频都要用）
    timeline = _timeline(shots, out, cfg["video"])
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    if "subtitle" in stages:
        lines = [(t["start"], t["start"] + t["speech"], t["line"]) for t in timeline if t["line"] and t["speech"]]
        srt = subtitle.build_srt(lines, cfg["subtitle"].get("max_chars", 16))
        (out / "subtitles.srt").write_text(srt, encoding="utf-8")
        _log("字幕：subtitles.srt 已生成")

    # 4. 预览视频
    if "video" in stages:
        if not video.has_ffmpeg():
            _log("没找到 ffmpeg，跳过预览视频（brew install ffmpeg）")
        else:
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

    _log(f"全部完成 ✅  打开文件夹：open \"{out}\"")
    return out


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
