"""阈值校准：用数据决定"相似度多少算同一个人"。

正样本（同一个人）：分镜表里每个角色的定妆照 + 质检时画过的所有候选图，两两比对
负样本（不同的人）：生成一组外貌明显不同、但设定相近的女性（对照组），和角色图、彼此之间两两比对
然后找一条最能把两组分开的线，作为建议阈值。
"""
from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

from . import storyboard as sbmod
from .config import ROOT

# 对照组：设定和剧中镜头相近（亚洲年轻女性、雨夜、风衣），只有长相明显不同 —— 这样才是"难"的负样本
OTHERS = [
    "a 22-year-old East Asian woman with a round face, long wavy dark brown hair with a middle part, wearing a navy trench coat",
    "a 30-year-old East Asian woman with a square jaw, short pixie-cut black hair, thin eyebrows, wearing a grey trench coat",
    "a 28-year-old East Asian woman with a heart-shaped face, high ponytail, monolid eyes, wearing a black trench coat",
    "a 35-year-old East Asian woman with a long face, shoulder-length layered hair with side-swept bangs, wearing a camel coat",
    "a 24-year-old East Asian woman with a small V-shaped face, long straight hair dyed chestnut, large round glasses, wearing a beige trench coat",
    "a 32-year-old East Asian woman with high cheekbones, low bun, freckles, wearing an olive trench coat",
    "a 26-year-old East Asian woman with a wide face and full lips, chin-length wavy bob, wearing a white trench coat",
    "a 29-year-old East Asian woman with a narrow face, very long straight black hair, sharp eyebrows, wearing a dark green trench coat",
]
SCENE = ("Cinematic vertical film still of {look}, standing on a rain-soaked neon city street at night, "
         "looking toward the camera, medium close-up, shallow depth of field, 35mm film look.")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _positives(sb: dict, out: Path) -> dict[str, list[Path]]:
    """每个角色：定妆照 + 所有包含该角色的镜头候选图。"""
    groups: dict[str, list[Path]] = {}
    cand = out / "images" / "_candidates"
    for cid in sb["characters"]:
        imgs = []
        cast = out / "cast" / f"{cid}.png"
        if cast.exists():
            imgs.append(cast)
        for s in sb["shots"]:
            if cid in s["characters"]:
                imgs += sorted(cand.glob(f"{sbmod.shot_name(s)}_*.png"))
        if imgs:
            groups[cid] = imgs
    return groups


def run(sb: dict, out: Path, cfg: dict, comfy, people: int = 6) -> dict:
    from .face import FaceMatcher

    faces = FaceMatcher()
    cal_dir = ROOT / "output" / "_calibrate"
    cal_dir.mkdir(parents=True, exist_ok=True)

    # 1. 正样本
    pos_groups = _positives(sb, out)
    n_pos_imgs = sum(len(v) for v in pos_groups.values())
    if n_pos_imgs < 2:
        raise RuntimeError("同一角色的图不够（至少 2 张）。先跑一次带质检的 run，生成定妆照和候选图")
    _log(f"正样本：{n_pos_imgs} 张角色图（{', '.join(f'{k}×{len(v)}' for k, v in pos_groups.items())}）")

    # 2. 负样本：对照组（已生成的会跳过）
    people = min(people, len(OTHERS))
    others = []
    for i in range(people):
        p = cal_dir / f"other_{i + 1:02d}.png"
        if not p.exists():
            if comfy is None:
                raise RuntimeError("需要 ComfyUI 生成对照组，请先打开 ComfyUI")
            t0 = time.time()
            comfy.generate(SCENE.format(look=OTHERS[i]), p, seed=1000 + i)
            _log(f"  对照组 {i + 1}/{people} 生成完成，用时 {time.time() - t0:.0f}s")
        others.append(p)
    _log(f"负样本：{people} 个不同的人")

    # 3. 提取特征（每张图取最大的那张脸），用"相对项目目录的路径"作为 key，和 labels.json 对得上
    cache: dict[str, object] = {}

    def feat(p: Path):
        k = str(p)
        if k not in cache:
            f = faces.features(p)
            cache[k] = f[0]["feature"] if f else None
        return cache[k]

    def rel(p: Path) -> str:
        return str(p.relative_to(out)) if out in p.parents else f"_calibrate/{p.name}"

    char_imgs = [p for imgs in pos_groups.values() for p in imgs]
    skipped = [rel(p) for p in char_imgs + others if feat(p) is None]
    if skipped:
        _log(f"  这些图没检测到脸，已跳过：{', '.join(skipped)}")
    char_imgs = [p for p in char_imgs if feat(p) is not None]
    others = [p for p in others if feat(p) is not None]

    def pair(a: Path, b: Path, src: str) -> dict:
        return {"a": rel(a), "b": rel(b), "score": round(faces.similarity(feat(a), feat(b)), 4), "source": src}

    # 4. 组成样本对
    from .label import load_labels
    labels = load_labels(out)
    same, diff, labeled = [], [], []
    if labels:
        by_rel = {rel(p): p for p in char_imgs}
        for key, v in labels.items():
            ra, rb = key.split("|")
            if v == "skip" or ra not in by_rel or rb not in by_rel:
                continue
            item = pair(by_rel[ra], by_rel[rb], "人工标注")
            item["human"] = v
            labeled.append(item)
            (same if v == "same" else diff).append(item)
        _log(f"使用人工标注：像 {len(same)} 对、不像 {len(diff)} 对（说不准的已忽略）")
    else:
        _log("⚠️ 没有人工标注，暂时把同一角色的图都当成同一个人（不可靠）。建议先运行 studio label")
        for imgs in pos_groups.values():
            imgs = [p for p in imgs if feat(p) is not None]
            same += [pair(a, b, "同一角色（未确认）") for a, b in itertools.combinations(imgs, 2)]
    # 对照组：彼此之间、以及和角色图之间，都算"不同的人"
    diff += [pair(a, b, "对照组") for a, b in itertools.combinations(others, 2)]
    diff += [pair(a, b, "对照组 vs 角色") for a in others for b in char_imgs]
    if not same or not diff:
        raise RuntimeError("样本不够，无法校准（人工标注里至少要有一对\"像\"）")

    result = analyze([x["score"] for x in same], [x["score"] for x in diff], cfg.get("qa", {}))
    result.update(same_pairs=same, diff_pairs=diff, labeled=bool(labels))
    if labeled:  # 自动判断和人的判断一致的比例
        for c in result["compare"]:
            ok = sum((x["score"] >= c["threshold"]) == (x["human"] == "same") for x in labeled)
            c["agreement"] = round(ok / len(labeled), 3)
    (cal_dir / "calibration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (cal_dir / "distribution.svg").write_text(svg(result), encoding="utf-8")
    (cal_dir / "calibration.md").write_text(report(result), encoding="utf-8")
    _log(f"校准报告：{(cal_dir / 'calibration.md').relative_to(ROOT)}（分布图 distribution.svg）")
    return result


# ---------------------------------------------------------------- 统计
def _stats(xs: list[float]) -> dict:
    s = sorted(xs)
    pct = lambda q: s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]  # noqa: E731
    return {"n": len(s), "min": s[0], "p5": pct(0.05), "median": pct(0.5), "p95": pct(0.95), "max": s[-1],
            "mean": round(sum(s) / len(s), 4)}


def errors(same: list[float], diff: list[float], t: float) -> dict:
    """在阈值 t 下：误拒 = 同一个人被判成不像；误收 = 不同的人被判成像。"""
    fr = sum(x < t for x in same)
    fa = sum(x >= t for x in diff)
    return {"threshold": round(t, 3), "false_reject": fr, "false_reject_rate": round(fr / len(same), 3),
            "false_accept": fa, "false_accept_rate": round(fa / len(diff), 3)}


def analyze(same: list[float], diff: list[float], qa_cfg: dict) -> dict:
    # 在 0.05~0.95 之间扫描，找"误拒率 + 误收率"最小的点；并列时取中间，离两边都远一点
    grid = [round(0.05 + i * 0.005, 3) for i in range(181)]
    scored = [(errors(same, diff, t), t) for t in grid]
    best = min(e["false_reject_rate"] + e["false_accept_rate"] for e, _ in scored)
    ties = [t for e, t in scored if e["false_reject_rate"] + e["false_accept_rate"] == best]
    suggested = round(ties[len(ties) // 2], 3)
    s_same, s_diff = _stats(same), _stats(diff)
    separable = s_diff["p95"] < s_same["p5"]
    if s_diff["max"] < s_same["min"]:
        verdict = "两组完全分开：阈值放在中间，既不会误杀也不会放过"
    elif s_diff["p95"] < s_same["p5"]:
        verdict = "两组基本分开，只有个别重叠：阈值可用"
    else:
        verdict = "两组重叠明显：SFace 对这类 AI 生成的脸区分力有限，调阈值作用不大，需要从生成侧解决一致性"
    return {
        "same": s_same, "diff": s_diff,
        "suggested": suggested, "verdict": verdict, "separable": separable,
        "compare": [errors(same, diff, t) for t in sorted({0.30, 0.363, float(qa_cfg.get("face_threshold", 0.40)), suggested})],
    }


# ---------------------------------------------------------------- 报告
def report(r: dict) -> str:
    has_agree = "agreement" in r["compare"][0]
    rows = [f"| {c['threshold']} | {c['false_reject']}（{c['false_reject_rate']:.0%}） | {c['false_accept']}（{c['false_accept_rate']:.0%}） |"
            + (f" {c['agreement']:.0%} |" if has_agree else "")
            for c in r["compare"]]
    src = "人工标注为\"像\"的图对" if r.get("labeled") else "同一角色的图（⚠️ 未经人工确认，不可靠）"
    st = lambda s: f"{s['n']} 对 · 最低 {s['min']:.2f} · 中位 {s['median']:.2f} · 最高 {s['max']:.2f}"  # noqa: E731
    return "\n".join([
        "# 人脸相似度阈值校准",
        "",
        "![分布图](distribution.svg)",
        "",
        f"- **同一个人**：{st(r['same'])}　来源：{src}",
        f"- **不同的人**：{st(r['diff'])}",
        "",
        f"**结论**：{r['verdict']}",
        "",
        (f"**建议阈值：{r['suggested']}**（误拒率 + 误收率最小的点）" if r["separable"]
         else f"**建议阈值：不可靠**（数学上的最优点是 {r['suggested']}，但两组重叠太多，这个数没有意义）"),
        "",
        "| 阈值 | 误拒（同一个人被判不像） | 误收（不同的人被判像） |" + (" 和你的判断一致 |" if has_agree else ""),
        "|---|---|---|" + ("---|" if has_agree else ""),
        *rows,
        "",
        "> 误拒多 → 白白重画、浪费时间；误收多 → 换脸的镜头被放过。短剧里\"换脸\"更伤观感，可以适当偏向误拒。",
        "",
    ])


def svg(r: dict, w: int = 760, h: int = 360) -> str:
    """两组分数的直方图（不依赖任何画图库）。"""
    same = [p["score"] for p in r["same_pairs"]]
    diff = [p["score"] for p in r["diff_pairs"]]
    bins = [i / 20 for i in range(21)]  # 0~1，步长 0.05

    def hist(xs):
        c = [0] * 20
        for x in xs:
            c[min(19, max(0, int(x * 20)))] += 1
        return [v / len(xs) for v in c]

    hs, hd = hist(same), hist(diff)
    top = max(hs + hd) or 1
    L, R, T, B = 50, 20, 64, 50
    pw, ph = w - L - R, h - T - B
    X = lambda v: L + v * pw  # noqa: E731
    Y = lambda v: T + ph - v / top * ph  # noqa: E731
    bw = pw / 20 / 2 - 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
             f'<rect width="{w}" height="{h}" fill="white"/>',
             f'<text x="{L}" y="22" font-size="15" font-weight="bold">SFace 相似度分布：同一个人 vs 不同的人</text>']
    for i in range(20):
        x0 = X(bins[i]) + 1
        parts.append(f'<rect x="{x0:.1f}" y="{Y(hd[i]):.1f}" width="{bw:.1f}" height="{T + ph - Y(hd[i]):.1f}" fill="#f0a35e"/>')
        parts.append(f'<rect x="{x0 + bw + 1:.1f}" y="{Y(hs[i]):.1f}" width="{bw:.1f}" height="{T + ph - Y(hs[i]):.1f}" fill="#4a90d9"/>')
    parts.append(f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" stroke="#333"/>')
    for v in [0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        parts.append(f'<text x="{X(v):.1f}" y="{T + ph + 18}" text-anchor="middle">{v:.1f}</text>')
    tag = "建议" if r.get("separable") else "数学最优（不可靠）"
    lines = [(0.363, "#888", "官方 0.363"), (r["suggested"], "#2a9d4b", f"{tag} {r['suggested']}")]
    for k, (v, col, label) in enumerate(lines):
        parts.append(f'<line x1="{X(v):.1f}" y1="{T - 4}" x2="{X(v):.1f}" y2="{T + ph}" stroke="{col}" stroke-width="2" stroke-dasharray="5,4"/>')
        anchor = "start" if X(v) < L + pw / 2 else "end"
        parts.append(f'<text x="{X(v) + (4 if anchor == "start" else -4):.1f}" y="{T - 8 + k * 0}" fill="{col}" text-anchor="{anchor}">{label}</text>')
    lx = L + pw - 190
    parts += [f'<rect x="{lx}" y="{T}" width="12" height="12" fill="#4a90d9"/><text x="{lx + 18}" y="{T + 11}">同一个人（{len(same)} 对）</text>',
              f'<rect x="{lx}" y="{T + 20}" width="12" height="12" fill="#f0a35e"/><text x="{lx + 18}" y="{T + 31}">不同的人（{len(diff)} 对）</text>',
              f'<text x="{L + pw / 2}" y="{h - 10}" text-anchor="middle" fill="#555">余弦相似度（每组按占比画，组内合计 100%）</text>',
              '</svg>']
    return "\n".join(parts)
