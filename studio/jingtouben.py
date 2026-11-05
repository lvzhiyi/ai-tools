"""导入镜头本（jingtouben）导出的分镜，转成本项目的分镜表。

镜头本负责"生成之前"的整理：中文分镜（景别、画面、台词、为什么这样拍）+ 角色卡（外貌、服装）。
这里把它变成流水线能直接跑的分镜表：英文 image / motion、角色 look、{角色id} 占位符、音色和语气。

导出格式（JSON，字段名对应镜头本的 drift 表，字段宽进严出）：
{
  "format": "jingtouben.storyboard", "version": 1,
  "project": {"title": "雨夜", "story": "故事原文（可选，给模型补上下文）"},
  "characters": [{"name": "林夏", "appearance": "二十五岁，鹅蛋脸，黑色齐肩直发", "outfit": "米色风衣"}],
  "shots": [{"order": 1, "shotSize": "中景", "scene": "林夏在雨夜街头回头",
             "dialogue": "有人在跟着我……", "reason": "回头制造悬念", "characters": ["林夏"]}]
}

分工：确定的部分（台词、镜头顺序、出场角色、角色 id）由代码保证，不交给模型；
需要"理解和改写"的部分（翻译成英文提示词、补动作描述、选音色语气）交给 llm.chat，一次调用。
"""
from __future__ import annotations

import json
import re

from . import llm
from .plan import VOICES

FORMAT = "jingtouben.storyboard"

# 镜头本的景别（中文名 / 枚举名）→ 英文景别，写进 image 提示词
SHOT_SIZES = {
    "大远景": "extreme long shot", "extremeLong": "extreme long shot",
    "远景": "long shot", "long": "long shot",
    "全景": "full shot", "full": "full shot",
    "中景": "medium shot", "medium": "medium shot",
    "近景": "medium close-up", "mediumCloseUp": "medium close-up",
    "特写": "close-up", "closeUp": "close-up",
    "大特写": "extreme close-up", "extremeCloseUp": "extreme close-up",
}

_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w-]*)\}")


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


# ------------------------------------------------------------------ 读入
def parse(data: dict) -> dict:
    """校验并整理导出文件，得到统一结构。不调模型，出错直接给可读的原因。"""
    if not isinstance(data, dict):
        raise ValueError("导出文件应该是一个 JSON 对象")
    fmt = data.get("format")
    if fmt not in (None, FORMAT):
        raise ValueError(f"不是镜头本的导出文件（format={fmt}）")
    if int(data.get("version", 1)) != 1:
        raise ValueError(f"不支持的导出版本：{data.get('version')}（目前只支持 1）")

    project = data.get("project") or {}
    title = _text(_first(project, "title") or data.get("title"))
    if not title:
        raise ValueError("导出文件缺少项目标题（project.title）")

    cards, seen = [], set()
    for c in data.get("characters") or data.get("characterCards") or []:
        name = _text(c.get("name"))
        if not name:
            raise ValueError("有角色卡没有名字")
        if name in seen:
            raise ValueError(f"角色卡重名：{name}")
        seen.add(name)
        cards.append({"name": name, "appearance": _text(c.get("appearance")),
                      "outfit": _text(c.get("outfit")), "card": True})

    raw_shots = data.get("shots") or []
    if not raw_shots:
        raise ValueError("导出文件里没有镜头（shots 为空）")
    shots = []
    for i, s in enumerate(raw_shots, 1):
        scene = _text(s.get("scene"))
        if not scene:
            raise ValueError(f"第 {i} 个镜头缺少画面描述（scene）")
        size = _text(_first(s, "shotSize", "shot_size"))
        if size and size not in SHOT_SIZES:
            raise ValueError(f"第 {i} 个镜头的景别无法识别：{size}")
        names = [_text(n) for n in (_first(s, "characters", "characterNames", default=[]) or [])]
        names = list(dict.fromkeys(n for n in names if n))
        shots.append({"order": _first(s, "order", "sortOrder", default=i), "size": size, "scene": scene,
                      "dialogue": _text(s.get("dialogue")), "reason": _text(s.get("reason")), "names": names})
    shots.sort(key=lambda s: s["order"])
    for i, s in enumerate(shots, 1):  # 镜头本的 sortOrder 可能不连续，这里重新编号
        s["id"] = i

    # 镜头本允许"镜头里有人物但没建角色卡"，这些人物也要有 look，交给模型按故事推断
    for s in shots:
        for n in s["names"]:
            if n not in seen:
                seen.add(n)
                cards.append({"name": n, "appearance": "", "outfit": "", "card": False})
    return {"title": title, "story": _text(project.get("story")), "characters": cards, "shots": shots}


# ------------------------------------------------------------------ 调模型
SYSTEM_PROMPT = """你是短剧分镜师，负责把已经整理好的中文分镜改写成生图 / 图生视频要用的英文提示词。
输入是 JSON：角色列表和镜头列表。镜头顺序、台词、出场角色都已定好，不要增删镜头，不要改台词。

要求：
1. characters 按输入顺序逐个输出，name 原样照抄输入的中文名。每个角色给一个 id（小写英文或拼音，只用 a-z 0-9 _，如 linxia），不能重复。
   look 用英文一两句话写清固定外貌：年龄、脸型、发型发色、服装（颜色和款式），依据 appearance 和 outfit；
   这两项为空的角色，根据故事和镜头内容推断一个合理、具体的外貌。
2. image 用英文写生图提示词，开头写 "Cinematic vertical film still of ..."，写动作表情、场景、光线、画面风格，
   并写出给定的英文景别（shot_size）。镜头里出现的角色一律用 {{角色id}} 代替外貌描述
   （例如 "{{linxia}} looks back over her shoulder"），不要在 image 里重复外貌细节，也不要写角色中文名。
3. motion 用英文写这个镜头里"动起来"的部分（给图生视频用）：人物动作、表情变化、镜头运动，一句话，
   也用 {{角色id}} 指代角色。空镜写环境动态，例如 "rain keeps falling, neon signs flicker, slow pan right"。
4. voice 只能从这些音色里选：{voices}。同一个角色始终用同一个音色；没有出场角色的台词当旁白，用 Uncle_Fu。
5. emotion 用中文简短描述台词语气，比如"紧张，压低声音"；没有台词写空字符串。
6. 输出的 shots 按输入顺序，id 和输入一致。
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "id": {"type": "string"}, "look": {"type": "string"}},
                "required": ["name", "id", "look"],
            },
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer"},
                    "image": {"type": "string"},
                    "motion": {"type": "string"},
                    "voice": {"type": "string", "enum": VOICES},
                    "emotion": {"type": "string"},
                },
                "required": ["id", "image", "motion", "voice", "emotion"],
            },
        },
    },
    "required": ["characters", "shots"],
}


def _llm_input(src: dict) -> str:
    payload = {
        "story": src["story"][:4000],
        "characters": [{"name": c["name"], "appearance": c["appearance"], "outfit": c["outfit"]}
                       for c in src["characters"]],
        "shots": [{"id": s["id"], "shot_size": SHOT_SIZES.get(s["size"], ""), "scene": s["scene"],
                   "dialogue": s["dialogue"], "characters": s["names"]} for s in src["shots"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _match(src_chars: list[dict], out_chars: list[dict]) -> list[dict]:
    """把模型返回的角色和导出的角色一一对上：先按名字，名字被模型改了（如 林夏 → linxia）再按顺序。"""
    by_name = {c["name"].strip(): c for c in out_chars}
    names = {c["name"] for c in src_chars}
    matched = []
    for i, c in enumerate(src_chars):
        m = by_name.get(c["name"])
        if m is None and i < len(out_chars) and out_chars[i]["name"].strip() not in names:
            m = out_chars[i]
        matched.append(m or {"id": "", "look": ""})
    return matched


def _assign_ids(src_chars: list[dict], matched: list[dict]) -> dict[str, str]:
    """角色名 → id。用模型给的 id，但不合法、重复或缺失时由代码兜底成 char1、char2…"""
    ids, used = {}, set()
    for i, (c, m) in enumerate(zip(src_chars, matched), 1):
        cid = m["id"].strip().lower()
        if not _ID.match(cid) or cid in used:
            cid = f"char{i}"
            while cid in used:
                cid += "_"
        used.add(cid)
        ids[c["name"]] = cid
    return ids


def _fix_placeholders(text: str, ids: dict[str, str], allowed: set[str]) -> str:
    """模型偶尔把 {林夏} 写成中文名，或者引用了别的角色：中文名改成 id，未知占位符去掉花括号。"""
    def sub(m):
        key = m.group(1)
        if key in allowed:
            return m.group(0)
        if key in ids and ids[key] in allowed:
            return "{" + ids[key] + "}"
        return key
    return re.sub(r"\{([^{}\s]+)\}", sub, text)


def convert(src: dict, out: dict) -> dict:
    """把整理好的导出数据和模型输出合成分镜表。确定性字段只取导出数据。"""
    matched = _match(src["characters"], out.get("characters") or [])
    ids = _assign_ids(src["characters"], matched)
    characters = {}
    for c, m in zip(src["characters"], matched):
        look = m["look"].strip() or ", ".join(x for x in (c["appearance"], c["outfit"]) if x)
        if not look:
            raise ValueError(f"模型没有给角色 {c['name']} 写外貌（look）")
        characters[ids[c["name"]]] = {"name": c["name"], "look": look}

    by_id = {s["id"]: s for s in out.get("shots") or []}
    missing = [s["id"] for s in src["shots"] if s["id"] not in by_id]
    if missing:
        raise ValueError(f"模型漏了镜头：{missing}")
    shots = []
    for s in src["shots"]:
        o = by_id[s["id"]]
        cast = [ids[n] for n in s["names"]]
        allowed = set(cast)
        image = _fix_placeholders(o["image"].strip(), ids, allowed)
        if not image:
            raise ValueError(f"模型没有给第 {s['id']} 个镜头写 image")
        size = SHOT_SIZES.get(s["size"])
        if size and size not in image.lower():  # 景别是确定信息，模型漏写就由代码补上
            image = image.rstrip(" .") + f", {size}."
        shot = {"id": s["id"], "image": image, "characters": cast,
                "motion": _fix_placeholders(o["motion"].strip(), ids, allowed),
                "line": s["dialogue"]}
        if s["dialogue"]:
            shot["voice"] = o["voice"] if o["voice"] in VOICES else "Uncle_Fu"
            shot["emotion"] = o["emotion"].strip()
        if s["reason"]:
            shot["note"] = s["reason"]  # 镜头本的"为什么这样拍"，流水线不用，留给人看
        shots.append(shot)
    return {"title": src["title"], "source": FORMAT, "defaults": {"voice": "Vivian"},
            "characters": characters, "shots": shots}


def import_storyboard(data: dict, cfg: dict) -> tuple[dict, list[str]]:
    """导出文件 → 分镜表。返回 (分镜表, 提醒列表)。"""
    src = parse(data)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(voices=" / ".join(VOICES))},
        {"role": "user", "content": _llm_input(src)},
    ]
    out = llm.chat(cfg, messages, SCHEMA, temperature=0.3)
    sb = convert(src, out)
    warnings = [f"「{c['name']}」没有角色卡，外貌由模型推断，建议在镜头本里补上角色卡"
                for c in src["characters"] if not c["card"]]
    for s in sb["shots"]:
        if s["characters"] and not _PLACEHOLDER.search(s["image"]):
            warnings.append(f"第 {s['id']} 个镜头有出场角色但 image 里没有 {{角色id}}，外貌可能不一致，建议手动改")
    return sb, warnings
