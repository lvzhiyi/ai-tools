"""OpenAI 兼容的大模型客户端（默认指向本地 LM Studio）。

plan（拆分镜）和 vision（看图质检）共用这一个入口。
本地优先；仅在显式配置 cloud 时启用云端兜底。
"""
from __future__ import annotations

import base64
import json
import http.client
import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema.validators import validator_for


class LLMError(RuntimeError):
    pass


def cloud_status(cfg: dict, *, vision: bool = False) -> tuple[bool, str]:
    """只检查云端配置，不发送请求；看图必须显式指定支持图片的模型。"""
    cloud = cfg.get("cloud", {})
    if not cloud.get("enabled", False):
        return False, "未启用（仅使用本地模型）"
    try:
        url = urlsplit(cloud.get("url", ""))
    except ValueError:
        return False, "云端 url 格式无效"
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
        return False, "请配置有效的云端 url（凭证使用环境变量）"
    field = "vision_model" if vision else "model"
    if not cloud.get(field, "").strip():
        return False, f"未配置云端 {field}"
    key_env = cloud.get("api_key_env", "STUDIO_CLOUD_API_KEY")
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        return False, "api_key_env 应填写环境变量名（如 STUDIO_CLOUD_API_KEY），不能填写密钥本身"
    if not os.environ.get(key_env, "").strip():
        return False, f"未设置云端密钥环境变量 {key_env}"
    return True, "已配置（未发起云端请求）"


def _request(cfg: dict, messages: list[dict], schema: dict | None, *,
             model: str, temperature: float, timeout: float, cloud: bool,
             validator) -> dict | str:
    label = "云端" if cloud else "本地 LM Studio"
    body: dict = {"model": model, "temperature": temperature, "messages": messages}
    headers = {"Content-Type": "application/json"}
    if cloud:
        key = os.environ[cfg.get("api_key_env", "STUDIO_CLOUD_API_KEY")].strip()
        headers["Authorization"] = f"Bearer {key}"
    else:
        body["ttl"] = int(cfg.get("ttl_sec", 300))
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(), headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, float(cfg.get("timeout_sec", timeout)))) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        e.close()
        raise LLMError(f"{label} HTTP {e.code}") from None
    except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException):
        raise LLMError(f"{label} 连接失败或请求超时") from None
    except (ValueError, UnicodeError):
        raise LLMError(f"{label} 返回无效的接口 JSON") from None

    try:
        message = data["choices"][0]["message"]
        content = message["content"]
        if message.get("refusal") or not isinstance(content, str) or not content.strip():
            raise ValueError
        if data["choices"][0].get("finish_reason") in {"length", "content_filter"}:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise LLMError(f"{label} 返回空内容、拒绝、截断或无效响应结构") from None
    if schema is None:
        return content
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        result = json.loads(content)
    except ValueError:
        raise LLMError(f"{label} 模型没有返回合法 JSON") from None
    if not validator.is_valid(result):
        raise LLMError(f"{label} 模型 JSON 不符合 schema")
    return result


def chat(cfg: dict, messages: list[dict], schema: dict | None = None, *,
         model: str | None = None, temperature: float = 0.2, timeout: float = 600) -> dict | str:
    """支持仅本地、仅云端和自动兜底；默认沿用本地优先。"""
    validator = None
    if schema is not None:
        cls = validator_for(schema)
        cls.check_schema(schema)  # 编程错误不触发云端调用
        validator = cls(schema)
    mode = cfg.get("routing_mode", "auto")
    if mode not in {"local", "cloud", "auto"}:
        raise LLMError("未知模型模式")
    vision = any(isinstance(m.get("content"), list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"]
    ) for m in messages)

    def request_cloud():
        ready, reason = cloud_status(cfg, vision=vision)
        if not ready:
            raise LLMError(f"云端不可用：{reason}")
        cloud = cfg["cloud"]
        return _request(cloud, messages, schema, model=cloud["vision_model" if vision else "model"],
                        temperature=temperature, timeout=timeout, cloud=True, validator=validator)

    if mode == "cloud":
        return request_cloud()
    try:
        return _request(cfg, messages, schema, model=model or cfg["model"],
                        temperature=temperature, timeout=timeout, cloud=False, validator=validator)
    except LLMError as local_error:
        if mode == "local":
            raise
        ready, reason = cloud_status(cfg, vision=vision)
        if not ready:
            raise LLMError(f"{local_error}；云端不可用：{reason}") from None
        logging.getLogger(__name__).warning("%s；切换云端兜底%s", local_error, "（含图片）" if vision else "")
        try:
            return request_cloud()
        except LLMError as cloud_error:
            raise LLMError(f"本地和云端均失败：{local_error}；{cloud_error}") from None


def available(cfg: dict, *, vision: bool = False) -> bool:
    mode = cfg.get("routing_mode", "auto")
    if mode == "cloud":
        return cloud_status(cfg, vision=vision)[0]
    try:
        with urllib.request.urlopen(cfg["url"].rstrip("/") + "/models", timeout=3):
            return True
    except (OSError, ValueError, http.client.HTTPException):
        return mode != "local" and cloud_status(cfg, vision=vision)[0]


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
