"""用 LM Studio 里的本地大模型，把剧本拆成分镜表（JSON）。

LM Studio 的接口和 OpenAI 一样：POST {url}/chat/completions
用 response_format=json_schema 让模型严格按格式输出。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

SYSTEM_PROMPT = """你是短剧分镜师。把用户给的剧本拆成竖屏短剧的分镜表。

要求：
1. 每个镜头一句台词或一段旁白；没有台词的纯画面镜头 line 留空字符串。
2. image 用英文写，作为 AI 生图提示词：写清楚人物外貌和服装（同一角色每个镜头都要重复相同的外貌描述，保证前后一致）、动作表情、场景、景别、光线、画面风格。开头写 "Cinematic vertical film still of ..."。
3. voice 只能从这些音色里选：{voices}。同一个角色始终用同一个音色，旁白用 Uncle_Fu。
4. emotion 用中文简短描述语气，比如"紧张，压低声音"。
5. 镜头数量控制在 {max_shots} 个以内。
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "image": {"type": "string"},
                    "line": {"type": "string"},
                    "voice": {"type": "string"},
                    "emotion": {"type": "string"},
                },
                "required": ["id", "image", "line", "voice", "emotion"],
            },
        },
    },
    "required": ["title", "shots"],
}

VOICES = ["Vivian", "Serena", "Uncle_Fu", "Eric", "Dylan"]


def plan(script: str, cfg: dict, max_shots: int = 12) -> dict:
    body = {
        "model": cfg["model"],
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(voices=" / ".join(VOICES), max_shots=max_shots)},
            {"role": "user", "content": script},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "storyboard", "strict": True, "schema": SCHEMA},
        },
    }
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.load(resp)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"连不上 LM Studio（{cfg['url']}）。请打开 LM Studio → Developer → 打开 Server，并加载模型。{e}"
        ) from e

    content = data["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    return json.loads(content)
