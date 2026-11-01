"""用 LM Studio 里的本地大模型，把剧本拆成分镜表（JSON）。

LM Studio 的接口和 OpenAI 一样：POST {url}/chat/completions
用 response_format=json_schema 让模型严格按格式输出。
"""
from __future__ import annotations

from . import llm

SYSTEM_PROMPT = """你是短剧分镜师。把用户给的剧本拆成竖屏短剧的分镜表。

要求：
1. 先列出有露脸的主要角色 characters：id 用小写英文（如 linxia），look 用英文写清楚固定外貌：
   年龄、脸型、发型发色、服装（颜色和款式），一两句话，作为这个角色每次出场的统一描述。
2. 每个镜头一句台词或一段旁白；没有台词的纯画面镜头 line 留空字符串。
3. image 用英文写生图提示词，开头写 "Cinematic vertical film still of ..."，写动作表情、场景、景别、光线、画面风格。
   镜头里出现某个角色时，用 {{角色id}} 代替外貌描述（例如 "{{linxia}} looks back over her shoulder"），
   程序会自动替换成 look，保证每个镜头外貌一致；并把这些角色 id 填进该镜头的 characters。
4. voice 只能从这些音色里选：{voices}。同一个角色始终用同一个音色，旁白用 Uncle_Fu。
5. emotion 用中文简短描述语气，比如"紧张，压低声音"。
6. motion 用英文写这个镜头里"动起来"的部分（给图生视频用）：人物动作、表情变化、镜头运动，一句话，
   只写画面里能看到的变化，例如 "{{linxia}} slowly turns her head and glances back, camera pushes in slightly"。
   空镜也要写环境的动态，例如 "rain keeps falling, neon signs flicker, slow pan right"。
7. 镜头数量控制在 {max_shots} 个以内。
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "look": {"type": "string"},
                },
                "required": ["id", "name", "look"],
            },
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "image": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "line": {"type": "string"},
                    "voice": {"type": "string"},
                    "emotion": {"type": "string"},
                    "motion": {"type": "string"},
                },
                "required": ["id", "image", "characters", "line", "voice", "emotion", "motion"],
            },
        },
    },
    "required": ["title", "characters", "shots"],
}

VOICES = ["Vivian", "Serena", "Uncle_Fu", "Eric", "Dylan"]


def plan(script: str, cfg: dict, max_shots: int = 12) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(voices=" / ".join(VOICES), max_shots=max_shots)},
        {"role": "user", "content": script},
    ]
    sb = llm.chat(cfg, messages, SCHEMA, temperature=0.4)
    # 角色从数组转成 {id: {...}}，方便按 id 查
    sb["characters"] = {c["id"]: {"name": c["name"], "look": c["look"]} for c in sb.get("characters", [])}
    return sb
