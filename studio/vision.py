"""画面质检：让能看图的本地模型（LM Studio 里的 gemma-4 等）对照分镜描述打分、挑毛病。"""
from __future__ import annotations

from pathlib import Path

from . import llm

SYSTEM = """You are a strict quality reviewer for AI-generated film stills used in a short drama.
Compare the image with the shot description and report honestly.

match_score (1-5): how well the image matches the description
  5 = everything described is present and correct
  3 = main subject and setting are right, some details missing or wrong
  1 = wrong subject, wrong setting, or unusable
defects: concrete visible problems, each a short phrase, e.g. "extra fingers on left hand",
  "arm bent unnaturally", "duplicated person", "garbled text", "face distorted", "wrong clothing color".
  Empty list if none. Do not list style preferences as defects.
summary: one sentence in Chinese describing what the image actually shows."""

SCHEMA = {
    "type": "object",
    "properties": {
        "match_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "defects": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["match_score", "defects", "summary"],
}


def review(image: Path, description: str, cfg: dict, model: str | None = None) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": f"Shot description:\n{description}"},
            llm.image_part(image),
        ]},
    ]
    result = llm.chat(cfg, messages, SCHEMA, model=model, temperature=0.0, timeout=300)
    result["match_score"] = int(result.get("match_score", 0))
    result["defects"] = [d for d in result.get("defects", []) if d]
    return result
