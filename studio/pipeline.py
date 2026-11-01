"""流水线：分镜表 → 出图 → 配音 →（图生视频）→ 字幕 → 预览视频。

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

STAGES = ("images", "audio", "i2v", "subtitle", "video")


def _slug(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("_") or "project"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def project_dir(sb: dict) -> Path:
    return ROOT / "output" / _slug(sb["title"])


def run(sb_path: Path, cfg: dict, only: str | None = None, force: bool = False, command: str = "",
        shots: set[str] | None = None) -> Path:
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
        _run(sb, out, cfg, only, force, m, shots)
    except BaseException as e:
        status, error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        run_dir = m.finish(status, error)
        _log(f"运行报告：{(run_dir / 'report.md').relative_to(ROOT)}（总耗时 {m.summary()['total_sec']:.0f}s）")
    _log(f"全部完成 ✅  打开文件夹：open \"{out}\"")
    return out


def _run(sb: dict, out: Path, cfg: dict, only: str | None, force: bool, m, shots: set[str] | None = None) -> None:
    stages = [only] if only else list(STAGES)
    if not only and not cfg.get("i2v", {}).get("enabled", False):
        stages.remove("i2v")  # 图生视频较慢，默认不跑；--i2v 或 studio i2v 开启
    if "images" in stages:
        with m.stage("images"):
            _images(sb, out, cfg, force, m, free_after=len(stages) > 1)
    if "audio" in stages:
        with m.stage("audio"):
            _audio(sb, out, cfg, force, m)
    timeline = _timeline(sb["shots"], out, cfg["video"])  # 字幕和视频都要用
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    if "i2v" in stages:
        with m.stage("i2v"):
            _i2v(sb, out, cfg, timeline, force, m, shots)
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


def _release_memory(cfg: dict) -> None:
    """图生视频峰值要 13GB 左右：先让 ComfyUI、LM Studio 卸载模型，再清掉本进程的 MLX 缓存（配音模型）。"""
    import gc

    from .comfy import ComfyClient
    from .llm import unload_all

    for fn in (lambda: ComfyClient(cfg["comfy"]).free(), unload_all):
        try:
            fn()
        except Exception:
            pass
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass


def _i2v(sb: dict, out: Path, cfg: dict, timeline: list[dict], force: bool, m, shots: set[str] | None) -> None:
    """每个镜头：图 + 动作描述 → 视频片段 clips/NN.mp4，长度按时间线（台词时长）定。"""
    from . import i2v
    from .samples import reference_images

    icfg = cfg.get("i2v", {})
    engine = i2v.make_engine(icfg)
    problems = engine.check()
    if problems:
        raise i2v.I2VError("；".join(problems))
    clips = out / "clips"
    clips.mkdir(exist_ok=True)
    dur = {t["name"]: t["duration"] for t in timeline}
    fps, max_frames = int(icfg.get("fps", 24)), int(icfg.get("max_frames", 121))

    todo = []
    for s in sb["shots"]:
        name = sbmod.shot_name(s)
        if shots and name not in shots and str(s["id"]) not in shots:
            continue
        if not (out / "images" / f"{name}.png").exists():
            _log(f"  镜头 {name} 还没有图片，跳过")
            continue
        if force or not (clips / f"{name}.mp4").exists():
            todo.append(s)
    _log(f"图生视频：{len(todo)} 个镜头待生成（{icfg.get('model')}，{icfg.get('width', 448)}×{icfg.get('height', 768)}）")
    if not todo:
        return
    no_motion = [sbmod.shot_name(s) for s in todo if not s.get("motion")]
    if no_motion:
        _log(f"  提示：镜头 {'、'.join(no_motion)} 没写 motion（动作），只会轻微晃动")
    _release_memory(cfg)

    faces = None
    if icfg.get("face_check", True) and any(s["characters"] for s in todo):
        from .face import FaceMatcher
        faces = FaceMatcher()
    thr = float(cfg.get("qa", {}).get("face_threshold", 0.40))

    for i, s in enumerate(todo, 1):
        name = sbmod.shot_name(s)
        img, clip = out / "images" / f"{name}.png", clips / f"{name}.mp4"
        frames = i2v.frames_for(dur.get(name, 2.0), fps, max_frames)
        seed = int(icfg.get("seed") or s.get("seed") or 42)  # --seed 覆盖 > 镜头自己的 seed > 42
        r = engine.generate(img, i2v.build_prompt(s, sb["characters"]), frames, clip, seed=seed)
        ev = {"shot": name, "sec": r["sec"], "frames": frames, "video_sec": round(frames / fps, 2), "seed": seed,
              "peak_gb": r["peak_gb"], "motion": bool(s.get("motion"))}
        msg = ""
        if faces:
            refs = [p for cid in s["characters"] for p in reference_images(sb, out, cid)]
            try:
                fc = i2v.face_check(clip, img, refs, faces, icfg)
            except Exception as e:  # 质检出错不影响片段本身
                fc = None
                msg = f"，人脸质检出错（{type(e).__name__}: {str(e)[:80]}）"
            if fc is not None:
                ev["face"] = fc
                if not fc["frames"]:
                    ev["review"] = True
                    msg = "，没有可比对的正脸帧 👀"
                else:
                    ev["review"] = fc["identity_min"] < thr
                    msg = (f"，人脸 {fc['identity_min']:.2f}~{fc['identity_median']:.2f}"
                           f"（比了 {fc['frames']} 帧，跳过 {fc['skipped']} 帧侧脸/背影）" + (" 👀" if ev["review"] else ""))
        m.event("i2v", **ev)
        _save_i2v_result(clips, name, ev, i2v.build_prompt(s, sb["characters"]))
        _log(f"  [{i}/{len(todo)}] 镜头 {name}：{frames} 帧，用时 {r['sec']:.0f}s{msg}")


def _save_i2v_result(clips: Path, name: str, ev: dict, prompt: str) -> None:
    """每段的耗时、人脸质检结果存到 clips/i2v.json（网页控制台的镜头墙要读）。"""
    p = clips / "i2v.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[name] = {**{k: v for k, v in ev.items() if k != "shot"}, "prompt": prompt,
                  "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    moving = 0
    for t in timeline:
        img = out / "images" / f"{t['name']}.png"
        motion = out / "clips" / f"{t['name']}.mp4"  # 有图生视频片段就用片段，没有就用静态图
        aud = out / "audio" / f"{t['name']}.wav"
        aud = aud if aud.exists() else None
        dst = clips_dir / f"{t['name']}.mp4"
        if motion.exists():
            clips.append(video.make_clip_from_video(motion, aud, t["duration"], dst, cfg["video"]))
            moving += 1
        elif img.exists():
            clips.append(video.make_clip(img, aud, t["duration"], dst, cfg["video"]))
        else:
            _log(f"  镜头 {t['name']} 没有图片，跳过")
    if clips:
        video.concat(clips, out / "subtitles.srt", out / "preview.mp4")
        _log(f"预览视频：preview.mp4 已生成（{moving}/{len(clips)} 个镜头是动态的；字幕是软字幕，播放器里可开关）")
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
