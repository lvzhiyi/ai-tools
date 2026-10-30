"""用 ffmpeg 把每个镜头的图片 + 配音拼成一条预览视频（剪辑前看节奏用）。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 出错：\n" + r.stderr[-1500:])


def make_clip(image: Path, audio: Path | None, seconds: float, out: Path, cfg: dict) -> Path:
    w, h, fps = cfg.get("width", 1080), cfg.get("height", 1920), cfg.get("fps", 30)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(image)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += [
        "-t", f"{seconds:.3f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out),
    ]
    _run(cmd)
    return out


def concat(clips: list[Path], srt: Path | None, out: Path) -> Path:
    listfile = out.parent / "_clips.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile)]
    if srt and srt.exists():
        cmd += ["-i", str(srt), "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text",
                "-metadata:s:s:0", "language=chi"]
    else:
        cmd += ["-c", "copy"]
    cmd.append(str(out))
    _run(cmd)
    listfile.unlink(missing_ok=True)
    return out


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
