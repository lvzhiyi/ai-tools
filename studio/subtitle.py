"""根据台词和音频时长直接生成 SRT 字幕（台词是已知的，不需要再语音识别）。"""
from __future__ import annotations

import re

_BREAK = "。！？!?；;，,、：:…"
_STRIP_TAIL = "。，,、；;：:"


def split_text(text: str, max_chars: int = 16) -> list[str]:
    """把一句台词按标点切成适合屏幕显示的短句，每段不超过 max_chars 个字。"""
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    # 先按标点切成小分句（标点留在分句末尾）
    clauses = re.findall(rf"[^{_BREAK}]+[{_BREAK}]*|[{_BREAK}]+", text)
    chunks: list[str] = []
    buf = ""
    for c in clauses:
        if len(buf) + len(c) <= max_chars:
            buf += c
            continue
        if buf:
            chunks.append(buf)
        while len(c) > max_chars:  # 单个分句太长就硬切
            chunks.append(c[:max_chars])
            c = c[max_chars:]
        buf = c
    if buf:
        chunks.append(buf)
    cleaned = [c.strip().rstrip(_STRIP_TAIL).strip() for c in chunks]
    return [c for c in cleaned if c]


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(lines: list[tuple[float, float, str]], max_chars: int = 16) -> str:
    """lines: [(开始秒, 结束秒, 台词)]。按字数比例分配每段字幕的时间。"""
    entries = []
    for start, end, text in lines:
        chunks = split_text(text, max_chars)
        total = sum(len(c) for c in chunks) or 1
        t = start
        for c in chunks:
            d = (end - start) * len(c) / total
            entries.append((t, t + d, c))
            t += d
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(entries, 1))
