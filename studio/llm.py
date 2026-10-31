"""OpenAI 兼容的大模型客户端（默认指向本地 LM Studio）。

plan（拆分镜）和 vision（看图质检）共用这一个入口。
以后要做"本地优先、云端兜底"，只需要在这里加一层路由。
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


class LLMError(RuntimeError):
    pass


def chat(cfg: dict, messages: list[dict], schema: dict | None = None, *,
         model: str | None = None, temperature: float = 0.2, timeout: float = 600) -> dict | str:
    body: dict = {
        "model": model or cfg["model"],
        "temperature": temperature,
        "messages": messages,
        "ttl": int(cfg.get("ttl_sec", 300)),  # LM Studio：空闲多久后自动卸载模型
    }
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise LLMError(f"LM Studio 返回 {e.code}：{e.read().decode('utf-8', 'replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise LLMError(
            f"连不上 LM Studio（{cfg['url']}）。请打开 LM Studio → Developer → 打开 Server。{e.reason}"
        ) from e

    content = data["choices"][0]["message"]["content"] or ""
    if not schema:
        return content
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"模型没有返回合法 JSON：{content[:300]}") from e


def available(cfg: dict) -> bool:
    try:
        with urllib.request.urlopen(cfg["url"].rstrip("/") + "/models", timeout=3):
            return True
    except Exception:
        return False


def image_part(path: Path, max_side: int = 768) -> dict:
    """把图片缩小后转成 base64，作为多模态消息的一部分（缩图能大幅减少 token 和耗时）。"""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise LLMError(f"读不了图片：{path}")
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    b64 = base64.b64encode(buf.tobytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def unload_all() -> None:
    """尽量让 LM Studio 卸载模型，把内存还给 ComfyUI（失败也没关系，ttl 到期会自动卸载）。"""
    lms = shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / "lms")
    if Path(lms).exists():
        subprocess.run([lms, "unload", "--all"], capture_output=True, timeout=30)
