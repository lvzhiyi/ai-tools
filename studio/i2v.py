"""图生视频：把每个镜头的图变成几秒的动态镜头（"动态分镜"，看动作和节奏用）。

引擎：LTX-2.3（Lightricks）的 MLX 移植版 ltx-2-mlx，int4 权重，Apple 芯片原生运行。
它装在独立目录（默认 ~/ai-engines/ltx-2-mlx，有自己的 uv 环境），这里通过子进程调用：
- 依赖隔离：它的 mlx 版本不会和本项目的 mlx-audio 打架；
- 内存隔离：子进程结束，显存全部归还，不影响后面的步骤。

实测（M5 Pro 24GB，448×768，49 帧 = 2 秒）：约 55 秒/段，峰值显存约 13GB（VAE 解码时）；
LTX2_COMPUTE_DTYPE=float16 比默认快约 10%，画面 SSIM 0.95，人脸相似度持平。

视频质检：每隔几帧抽一帧，和角色定妆照比 SFace 相似度。
转头、背影时分数下降是正常的，所以只统计"正脸 / 半侧脸"的帧（按偏航角过滤）。
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import storyboard as sbmod

_PEAK = re.compile(r"peak Metal memory ([\d.]+) GB")


class I2VError(RuntimeError):
    pass


def frames_for(seconds: float, fps: int, max_frames: int) -> int:
    """LTX 的帧数必须是 8k+1（VAE 时间维度压缩 8 倍）。取不短于镜头时长的最小值，再封顶。"""
    k = max(1, math.ceil((seconds * fps - 1) / 8))
    cap = max(1, (max_frames - 1) // 8)
    return 8 * min(k, cap) + 1


def build_prompt(shot: dict, characters: dict) -> str:
    """动作描述在前（视频模型最看重它），画面描述在后作为补充。

    分镜里写了 motion 就用它；没写就只用画面描述 + 轻微运动，效果一般，建议补上 motion。
    """
    scene = sbmod.render_prompt(shot, characters)
    scene = scene.replace("Cinematic vertical film still", "Cinematic vertical shot", 1)
    motion = shot.get("motion")
    if motion:
        motion = sbmod.render_prompt({"image": motion}, characters).rstrip(". ")
        return f"{motion}. {scene}"
    return f"{scene} Subtle natural movement, slight camera drift."


class LtxMlx:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dir = Path(cfg.get("engine_dir", "~/ai-engines/ltx-2-mlx")).expanduser()

    def check(self) -> list[str]:
        problems = []
        if not (self.dir / "pyproject.toml").exists():
            problems.append(f"没找到 ltx-2-mlx：{self.dir}（git clone https://github.com/dgrauet/ltx-2-mlx.git 后 uv sync）")
        if not shutil.which("uv"):
            problems.append("没找到 uv")
        return problems

    def generate(self, image: Path, prompt: str, frames: int, out: Path, seed: int) -> dict:
        c = self.cfg
        cmd = ["uv", "run", "--project", str(self.dir), "ltx-2-mlx", "generate",
               *c.get("extra_args", ["--distilled", "--low-ram"]),
               "--model", c.get("model", "dgrauet/ltx-2.3-mlx-q4"),
               "--image", str(image), "0", "1.0",
               "-p", prompt,
               "-W", str(c.get("width", 448)), "-H", str(c.get("height", 768)),
               "-f", str(frames), "--frame-rate", str(c.get("fps", 24)),
               "--seed", str(seed), "-o", str(out)]
        env = {**os.environ}
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        if c.get("compute_dtype"):
            env["LTX2_COMPUTE_DTYPE"] = c["compute_dtype"]
        log = out.with_suffix(".log")
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as f:
            f.write("$ " + " ".join(cmd) + "\n\n")
            f.flush()
            r = subprocess.run(cmd, cwd=self.dir, env=env, stdout=f, stderr=subprocess.STDOUT,
                               timeout=int(c.get("timeout_sec", 1800)))
        sec = time.time() - t0
        text = log.read_text(encoding="utf-8", errors="replace")
        if r.returncode != 0 or not out.exists():
            raise I2VError(f"ltx-2-mlx 失败（退出码 {r.returncode}），日志：{log}\n" + text[-1200:])
        peaks = [float(x) for x in _PEAK.findall(text)]
        return {"sec": round(sec, 1), "peak_gb": max(peaks) if peaks else None}


def make_engine(cfg: dict):
    engine = cfg.get("engine", "ltx_mlx")
    if engine == "ltx_mlx":
        return LtxMlx(cfg)
    raise I2VError(f"不认识的图生视频引擎：{engine}")


# ------------------------------------------------------------------ 视频质检
def sample_frames(clip: Path, every: int, dst: Path) -> list[Path]:
    # setpts 把选中的帧重新排成连续时间戳，这样不用 -vsync（ffmpeg 7 已删）/ -fps_mode（ffmpeg 4 没有）
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip),
                    "-vf", f"select=not(mod(n\\,{every})),setpts=N/FRAME_RATE/TB", str(dst / "%03d.png")],
                   check=True)
    return sorted(dst.glob("*.png"))


def face_check(clip: Path, still: Path, refs: list[Path], faces, cfg: dict) -> dict | None:
    """抽帧比人脸。返回 None 表示镜头里没有角色参照（空镜）。

    identity：和定妆照（多张取最高）的相似度 → 是不是这个角色
    drift：和这个镜头原图的相似度 → 动起来以后脸有没有跑偏
    只统计 |yaw| <= max_yaw 的帧；转过去的帧记为 skipped。
    """
    if not refs:
        return None
    ref_feats = [faces.reference(p) for p in refs]
    still_faces = faces.features(still)
    still_feat = still_faces[0]["feature"] if still_faces else None
    max_yaw = float(cfg.get("max_yaw", 0.35))
    rows, skipped = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for p in sample_frames(clip, int(cfg.get("sample_every", 6)), Path(tmp)):
            fs = faces.features(p)
            if not fs or abs(fs[0]["yaw"]) > max_yaw:
                skipped += 1
                continue
            f = fs[0]["feature"]
            rows.append({
                "identity": max(faces.similarity(f, r) for r in ref_feats),
                "drift": faces.similarity(f, still_feat) if still_feat is not None else None,
            })
    if not rows:
        return {"frames": 0, "skipped": skipped}
    ids = sorted(r["identity"] for r in rows)
    drifts = [r["drift"] for r in rows if r["drift"] is not None]
    return {
        "frames": len(rows), "skipped": skipped,
        "identity_min": round(ids[0], 3), "identity_median": round(ids[len(ids) // 2], 3),
        "drift_min": round(min(drifts), 3) if drifts else None,
    }
