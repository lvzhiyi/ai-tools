"""本地配音：Qwen3-TTS（mlx-audio，跑在 Apple Silicon 上）。

和 ~/ai-tools/tts.py（命令行 tts 命令）用的是同一个模型，这里封装成函数给流水线批量调用。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class Voice:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def say(self, text: str, out_path: Path, voice: str | None = None, instruct: str | None = None) -> Path:
        """把一句台词合成为 wav。"""
        from mlx_audio.tts.generate import generate_audio  # 延迟导入：check 等命令不需要加载

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)  # generate_audio 把文件写到当前目录
            try:
                generate_audio(
                    text=text,
                    model=self.cfg["model"],
                    voice=voice or self.cfg["default_voice"],
                    lang_code=self.cfg.get("lang_code", "chinese"),
                    instruct=instruct or None,
                    file_prefix="line",
                    audio_format="wav",
                    join_audio=True,
                    verbose=False,
                )
            finally:
                os.chdir(cwd)

            wavs = sorted(Path(tmp).glob("*.wav"))
            if not wavs:
                raise RuntimeError(f"配音没有生成文件：{text}")
            if len(wavs) == 1:
                shutil.move(str(wavs[0]), out_path)
            else:
                _concat(wavs, out_path)
        return out_path


def _concat(files: list[Path], out_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    parts, sr = [], None
    for f in files:
        data, sr = sf.read(f)
        parts.append(data)
    sf.write(out_path, np.concatenate(parts), sr)


def duration(path: Path) -> float:
    """音频时长（秒）。"""
    try:
        import soundfile as sf
        return float(sf.info(path).duration)
    except ImportError:
        import wave
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
