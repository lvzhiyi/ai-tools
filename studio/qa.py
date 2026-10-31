"""AI 自动质检：出图 → 检查 → 不合格换 seed 重画 → 选最好的一张。

为了不让几个大模型同时挤 24G 内存，每一轮分两个阶段：
  阶段 A：ComfyUI 把这一轮所有待画的镜头都画完
  阶段 B：释放 ComfyUI 内存 → 人脸比对（CPU，很轻）+ LM Studio 看图打分 → 卸载看图模型
不合格的镜头进入下一轮，最多 max_attempts 轮；还不合格就选分数最高的一张并在报告里标出来。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import storyboard as sbmod
from .config import ROOT

PORTRAIT = (
    "Cinematic portrait photograph, head and shoulders, front view, looking straight at the camera, "
    "neutral expression, soft even studio lighting, plain grey background, sharp focus on the face. {look}"
)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 定妆照
def ensure_cast(sb: dict, out: Path, comfy, faces, force: bool = False) -> dict[str, Path]:
    """每个角色一张定妆照：分镜表里给了 ref 就用它，否则自动生成到 output/<项目>/cast/。"""
    refs: dict[str, Path] = {}
    cast_dir = out / "cast"
    for cid, c in sb["characters"].items():
        if c.get("ref"):
            p = Path(c["ref"]).expanduser()
            p = p if p.is_absolute() else ROOT / p
            if not p.exists():
                raise FileNotFoundError(f"角色 {cid} 的定妆照不存在：{p}")
            refs[cid] = p
            continue
        p = cast_dir / f"{cid}.png"
        if p.exists() and not force:
            refs[cid] = p
            continue
        if comfy is None:
            raise RuntimeError(f"角色 {cid} 还没有定妆照，先运行：uv run studio cast <分镜表>")
        for attempt in range(1, 4):
            t0 = time.time()
            comfy.generate(PORTRAIT.format(look=c["look"]), p)
            if faces is None or faces.features(p):
                _log(f"  定妆照 {cid}（{c.get('name', '')}）完成，用时 {time.time() - t0:.0f}s → {p.relative_to(ROOT)}")
                break
            _log(f"  定妆照 {cid} 没检测到人脸，重画（{attempt}/3）")
        else:
            raise RuntimeError(f"角色 {cid} 的定妆照连续 3 次检测不到人脸，请换一个 look 描述或手动指定 ref")
        refs[cid] = p
    return refs


# ---------------------------------------------------------------- 打分
class Inspector:
    def __init__(self, cfg: dict, sb: dict, refs: dict[str, Path], faces, use_vision: bool):
        self.q = cfg.get("qa", {})
        self.llm_cfg = cfg["lmstudio"]
        self.sb = sb
        self.faces = faces
        self.use_vision = use_vision
        if use_vision:
            from .llm import available
            if not available(self.llm_cfg):
                self.use_vision = False
                _log("  ⚪ LM Studio 没开，本次跳过看图质检（只做人脸比对）。想要看图质检：打开 LM Studio → Developer → Server")
        self.ref_feat = {cid: faces.reference(p) for cid, p in refs.items()} if faces else {}

    def inspect(self, shot: dict, image: Path) -> dict:
        thr = float(self.q.get("face_threshold", 0.40))
        res: dict = {"image": str(image.relative_to(ROOT)), "face": {}, "notes": []}
        ok = True

        if self.faces and shot["characters"]:
            found = self.faces.features(image)
            res["faces_found"] = len(found)
            for cid in shot["characters"]:
                sim = self.faces.best_match(self.ref_feat[cid], found)
                res["face"][cid] = None if sim is None else round(sim, 3)
                if sim is None:
                    res["notes"].append(f"{cid}：没检测到脸（背影/远景？），无法比对")
                elif sim < thr:
                    ok = False
                    res["notes"].append(f"{cid}：不像同一个人（{sim:.2f} < {thr}）")

        if self.use_vision:
            from .vision import review
            try:
                v = review(image, sbmod.render_prompt(shot, self.sb["characters"]),
                           self.llm_cfg, model=self.q.get("vision_model"))
                res["vision"] = v
                if v["match_score"] < int(self.q.get("vision_min_score", 3)):
                    ok = False
                    res["notes"].append(f"画面不符合描述（{v['match_score']}/5）")
                if v["defects"]:
                    res["notes"].append("瑕疵：" + "；".join(v["defects"]))
            except Exception as e:  # 看图模型出问题不应该卡死整条流水线
                res["notes"].append(f"看图质检失败，已跳过：{e}")

        res["passed"] = ok
        res["score"] = self._score(res, thr)
        return res

    @staticmethod
    def _score(res: dict, thr: float) -> float:
        """用来在多次尝试里挑最好的一张：合格优先，再看人脸相似度和画面分。"""
        sims = [s if s is not None else thr for s in res["face"].values()]
        face = sum(sims) / len(sims) if sims else 0.5
        vis = res.get("vision", {}).get("match_score", 3) / 5
        return round((1.0 if res["passed"] else 0.0) + face + 0.5 * vis, 4)


# ---------------------------------------------------------------- 出图 + 质检主流程
def produce_images(sb: dict, out: Path, cfg: dict, comfy, force: bool = False) -> dict:
    q = cfg.get("qa", {})
    shots = sb["shots"]
    img_dir = out / "images"
    todo = [s for s in shots if force or not (img_dir / f"{sbmod.shot_name(s)}.png").exists()]
    _log(f"出图：{len(todo)} 张待生成（已有 {len(shots) - len(todo)} 张跳过）")
    if not todo:
        return {}

    if not q.get("enabled", True):  # 关闭质检：一张画完就算
        for i, s in enumerate(todo, 1):
            t0 = time.time()
            comfy.generate(sbmod.render_prompt(s, sb["characters"]), img_dir / f"{sbmod.shot_name(s)}.png",
                           seed=s.get("seed"))
            _log(f"  [{i}/{len(todo)}] 镜头 {sbmod.shot_name(s)} 完成，用时 {time.time() - t0:.0f}s")
        return {}

    faces = None
    if sb["characters"] and q.get("face", True):
        from .face import FaceMatcher
        faces = FaceMatcher()
    refs = ensure_cast(sb, out, comfy, faces) if sb["characters"] else {}
    inspector = Inspector(cfg, sb, refs, faces, use_vision=q.get("vision", True))

    max_attempts = int(q.get("max_attempts", 3))
    cand_dir = img_dir / "_candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {sbmod.shot_name(s): {"attempts": []} for s in todo}
    pending = list(todo)

    for rnd in range(1, max_attempts + 1):
        if not pending:
            break
        _log(f"第 {rnd} 轮：画 {len(pending)} 张")
        made = []
        for i, s in enumerate(pending, 1):  # 阶段 A：出图
            name = sbmod.shot_name(s)
            cand = cand_dir / f"{name}_{rnd}.png"
            t0 = time.time()
            comfy.generate(sbmod.render_prompt(s, sb["characters"]), cand, seed=s.get("seed") if rnd == 1 else None)
            _log(f"  [{i}/{len(pending)}] 镜头 {name} 画好，用时 {time.time() - t0:.0f}s")
            made.append((s, cand, time.time() - t0))

        if inspector.use_vision:  # 阶段 B：质检
            comfy.free()
        _log("  质检中 ...")
        still = []
        for s, cand, gen_sec in made:
            name = sbmod.shot_name(s)
            t0 = time.time()
            r = inspector.inspect(s, cand)
            r.update(round=rnd, gen_sec=round(gen_sec, 1), qa_sec=round(time.time() - t0, 1))
            report[name]["attempts"].append(r)
            face_txt = " ".join(f"{k}={v}" for k, v in r["face"].items())
            vis_txt = f" 画面={r['vision']['match_score']}/5" if "vision" in r else ""
            mark = "✅" if r["passed"] else "❌"
            _log(f"  {mark} 镜头 {name} {face_txt}{vis_txt} {'；'.join(r['notes'])}")
            if r["passed"]:
                shutil.copy(cand, img_dir / f"{name}.png")
                report[name]["final"] = cand.name
            else:
                still.append(s)
        if inspector.use_vision:
            from .llm import unload_all
            unload_all()
        pending = still

    for s in pending:  # 几轮都不合格：挑分数最高的
        name = sbmod.shot_name(s)
        best = max(report[name]["attempts"], key=lambda a: a["score"])
        shutil.copy(ROOT / best["image"], img_dir / f"{name}.png")
        report[name]["final"] = Path(best["image"]).name
        report[name]["best_effort"] = True
        _log(f"  ⚠️ 镜头 {name} {max_attempts} 次都没达标，先用分数最高的 {Path(best['image']).name}，建议人工看一下")

    write_report(out, sb, report, q, cross_consistency(sb, out, faces) if faces else None)
    return report


# ---------------------------------------------------------------- 只检查不重画
def inspect_existing(sb: dict, out: Path, cfg: dict, comfy=None) -> dict:
    q = cfg.get("qa", {})
    faces = None
    if sb["characters"] and q.get("face", True):
        from .face import FaceMatcher
        faces = FaceMatcher()
    refs = ensure_cast(sb, out, comfy, faces) if sb["characters"] else {}
    if comfy is not None and q.get("vision", True):
        comfy.free()
    inspector = Inspector(cfg, sb, refs, faces, use_vision=q.get("vision", True))
    report = {}
    for s in sb["shots"]:
        name = sbmod.shot_name(s)
        img = out / "images" / f"{name}.png"
        if not img.exists():
            continue
        r = inspector.inspect(s, img)
        report[name] = {"attempts": [r], "final": img.name}
        mark = "✅" if r["passed"] else "❌"
        face_txt = " ".join(f"{k}={v}" for k, v in r["face"].items())
        vis_txt = f" 画面={r['vision']['match_score']}/5" if "vision" in r else ""
        _log(f"{mark} 镜头 {name} {face_txt}{vis_txt} {'；'.join(r['notes'])}")
    if inspector.use_vision:
        from .llm import unload_all
        unload_all()
    write_report(out, sb, report, q, cross_consistency(sb, out, faces) if faces else None)
    return report


# ---------------------------------------------------------------- 报告
def cross_consistency(sb: dict, out: Path, faces) -> dict[str, float]:
    """同一角色在所有镜头之间两两比对，取最低分：衡量"整集里最不像的两个镜头有多不像"。"""
    result = {}
    for cid in sb["characters"]:
        feats = []
        for s in sb["shots"]:
            img = out / "images" / f"{sbmod.shot_name(s)}.png"
            if cid in s["characters"] and img.exists():
                f = faces.features(img)
                if f:
                    feats.append(f[0]["feature"])
        if len(feats) >= 2:
            sims = [faces.similarity(a, b) for i, a in enumerate(feats) for b in feats[i + 1:]]
            result[cid] = round(min(sims), 3)
    return result


def write_report(out: Path, sb: dict, report: dict, q: dict, cross: dict | None = None) -> None:
    (out / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 质检报告 · {sb['title']}",
        "",
        f"人脸阈值 {q.get('face_threshold', 0.40)}（SFace 余弦相似度，OpenCV 官方同人阈值 0.363）；"
        f"画面最低分 {q.get('vision_min_score', 3)}/5",
        "",
        "| 镜头 | 尝试次数 | 结果 | 人脸相似度 | 画面分 | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    total_attempts, passed_first = 0, 0
    for name, r in report.items():
        atts = r["attempts"]
        total_attempts += len(atts)
        if atts and atts[0]["passed"]:
            passed_first += 1
        final = next((a for a in atts if Path(a["image"]).name == r.get("final")), atts[-1])
        face = " ".join(f"{k}={v}" for k, v in final["face"].items()) or "-"
        vis = f"{final['vision']['match_score']}/5" if "vision" in final else "-"
        status = "⚠️ 未达标" if r.get("best_effort") else ("✅" if final["passed"] else "❌")
        lines.append(f"| {name} | {len(atts)} | {status} | {face} | {vis} | {'；'.join(final['notes']) or '-'} |")
    n = len(report) or 1
    lines += ["", f"- 一次通过率：{passed_first}/{len(report)}（{passed_first / n:.0%}）",
              f"- 平均每个镜头出图次数：{total_attempts / n:.2f}"]
    if cross:
        lines += ["", "## 跨镜头一致性（同一角色所有镜头两两比对的最低相似度）", ""]
        lines += [f"- {cid}（{sb['characters'][cid].get('name', '')}）：{v}" for cid, v in cross.items()]
    (out / "qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"质检报告：{(out / 'qa_report.md').relative_to(ROOT)}")
