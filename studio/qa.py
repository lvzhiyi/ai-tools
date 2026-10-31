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


ANGLES = {
    "left": "head turned about 45 degrees to the left, three-quarter view",
    "right": "head turned about 45 degrees to the right, three-quarter view",
}
ANGLE_PROMPT = ("The same person from the reference image, {angle}, eyes looking toward the camera, neutral expression, "
                "same hairstyle and clothing, plain grey studio background, soft even lighting, head and shoulders portrait.")


def cast_angles(sb: dict, out: Path, front: dict[str, Path], editor, force: bool = False,
                faces=None, threshold: float = 0.45) -> dict[str, list[tuple[str, Path]]]:
    """在正脸定妆照基础上，用参考图编辑再画左右 3/4 侧脸，让侧脸镜头有对应角度的参照。
    侧脸照本身也要和正脸比一次：如果画歪了（不像正脸那个人），参照就会带偏后面所有镜头，所以不合格就重画。"""
    refs = {cid: [("定妆照", p)] for cid, p in front.items()}
    if editor is None:
        return refs
    for cid, p in front.items():
        front_feat = faces.reference(p) if faces else None
        for key, desc in ANGLES.items():
            ap = out / "cast" / f"{cid}_{key}.png"
            if force or not ap.exists():
                for attempt in range(1, 4):
                    t0 = time.time()
                    editor.generate(ANGLE_PROMPT.format(angle=desc), ap, ref_image=p)
                    found = faces.features(ap) if faces else [None]
                    sim = faces.best_match(front_feat, found) if faces else None
                    note = f"，和正脸相似度 {sim:.2f}" if sim is not None else ""
                    _log(f"  定妆照 {cid} {key} 侧脸完成，用时 {time.time() - t0:.0f}s{note}")
                    if faces is None or (sim is not None and sim >= threshold):
                        break
                    _log(f"    侧脸不像正脸，重画（{attempt}/3）")
                else:
                    _log(f"  ⚠️ {cid} {key} 侧脸 3 次都不像正脸，不作为参照")
                    ap.unlink(missing_ok=True)
                    continue
            refs[cid].append((f"定妆照-{key}", ap))
    return refs


# ---------------------------------------------------------------- 打分
class Inspector:
    """给一张图打分。人脸比对的参照有两类，取最像的那个：
    1. 定妆照（正脸 + 左右 3/4 侧脸）
    2. 角色图库：本集里已经通过质检的镜头 —— 景别、光线和后面的镜头更接近
    """

    def __init__(self, cfg: dict, sb: dict, refs: dict[str, list[tuple[str, Path]]], faces, use_vision: bool):
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
        # cid -> [(来源标签, 特征)]
        self.refs: dict[str, list] = {}
        if faces:
            for cid, items in refs.items():
                for label, p in items:
                    f = faces.features(p)
                    if f:
                        self.refs.setdefault(cid, []).append((label, f[0]["feature"]))
        self.gallery: dict[str, list] = {}
        self._matched: dict[str, dict] = {}  # 图片路径 -> {cid: 匹配上的那张脸的特征}

    def _best(self, cid: str, found: list[dict]):
        """返回 (相似度, 参照来源, 匹配上的脸)。"""
        best = (None, None, None)
        for face in found:
            for label, ref in self.refs.get(cid, []) + self.gallery.get(cid, []):
                sim = self.faces.similarity(ref, face["feature"])
                if best[0] is None or sim > best[0]:
                    best = (sim, label, face)
        return best

    def inspect(self, shot: dict, image: Path) -> dict:
        thr = float(self.q.get("face_threshold", 0.40))
        res: dict = {"image": str(image.relative_to(ROOT)), "face": {}, "face_ref": {}, "pose": {}, "notes": []}
        ok = True

        if self.faces and shot["characters"]:
            found = self.faces.features(image)
            res["faces_found"] = len(found)
            matched = {}
            for cid in shot["characters"]:
                sim, label, face = self._best(cid, found)
                res["face"][cid] = None if sim is None else round(sim, 3)
                if sim is None:
                    res["notes"].append(f"{cid}：没检测到脸（背影/远景？），无法比对")
                    continue
                matched[cid] = face["feature"]
                res["face_ref"][cid] = label
                res["pose"][cid] = {"face_h": face["face_h"], "yaw": face["yaw"]}
                if sim < thr:
                    ok = False
                    res["notes"].append(f"{cid}：不像同一个人（{sim:.2f} < {thr}）")
            self._matched[str(image)] = matched

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

    def remember(self, shot_name: str, image: Path) -> None:
        """镜头通过质检后，把里面的角色脸加入图库，供后面的镜头比对。"""
        if not self.q.get("gallery", True):
            return
        for cid, feat in self._matched.get(str(image), {}).items():
            self.gallery.setdefault(cid, []).append((f"镜头{shot_name}", feat))

    @staticmethod
    def _score(res: dict, thr: float) -> float:
        """用来在多次尝试里挑最好的一张：合格优先，再看人脸相似度和画面分。"""
        sims = [s if s is not None else thr for s in res["face"].values()]
        face = sum(sims) / len(sims) if sims else 0.5
        vis = res.get("vision", {}).get("match_score", 3) / 5
        return round((1.0 if res["passed"] else 0.0) + face + 0.5 * vis, 4)


# ---------------------------------------------------------------- 出图 + 质检主流程
def _plan_shot(s: dict, sb: dict, refs: dict, editor) -> tuple:
    """决定这个镜头怎么画：有角色且有参考图模型 → 编辑模式（照着定妆照画），否则文生图。"""
    if editor is not None and s["characters"]:
        cid = s["characters"][0]  # 目前一个镜头只用第一个角色的定妆照作参考
        if cid in refs:
            return "edit", cid, sbmod.render_prompt(s, sb["characters"], ref_char=cid), refs[cid]
    return "t2i", None, sbmod.render_prompt(s, sb["characters"]), None


def produce_images(sb: dict, out: Path, cfg: dict, comfy, force: bool = False, editor=None) -> dict:
    """comfy：文生图客户端；editor：参考图编辑客户端（没配置就是 None，全部走文生图）。"""
    q = cfg.get("qa", {})
    shots = sb["shots"]
    img_dir = out / "images"
    todo = [s for s in shots if force or not (img_dir / f"{sbmod.shot_name(s)}.png").exists()]
    _log(f"出图：{len(todo)} 张待生成（已有 {len(shots) - len(todo)} 张跳过）")
    if not todo:
        return {}

    faces = None
    if sb["characters"] and q.get("enabled", True) and q.get("face", True):
        from .face import FaceMatcher
        faces = FaceMatcher()
    # 定妆照：质检要用它比对，编辑模式要用它当参考图
    refs = ensure_cast(sb, out, comfy, faces) if sb["characters"] and (q.get("enabled", True) or editor) else {}

    def draw(s: dict, path: Path, seed) -> str:
        mode, cid, prompt, ref = _plan_shot(s, sb, refs, editor)
        client = editor if mode == "edit" else comfy
        client.generate(prompt, path, seed=seed, ref_image=ref)
        return "参考图" if mode == "edit" else "文生图"

    if not q.get("enabled", True):  # 关闭质检：一张画完就算
        for i, s in enumerate(todo, 1):
            t0 = time.time()
            how = draw(s, img_dir / f"{sbmod.shot_name(s)}.png", s.get("seed"))
            _log(f"  [{i}/{len(todo)}] 镜头 {sbmod.shot_name(s)}（{how}）完成，用时 {time.time() - t0:.0f}s")
        return {}

    angle_refs = cast_angles(sb, out, refs, editor if q.get("cast_angles", True) else None,
                             faces=faces, threshold=float(q.get("angle_threshold", 0.35)))
    inspector = Inspector(cfg, sb, angle_refs, faces, use_vision=q.get("vision", True))

    max_attempts = int(q.get("max_attempts", 3))
    cand_dir = img_dir / "_candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    # 重画的镜头：把上一次的候选图归档到 history/，避免新旧模型的图混在一起（标注、校准会读这个目录）
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for s in todo:
        for old in cand_dir.glob(f"{sbmod.shot_name(s)}_*.png"):
            dst = cand_dir / "history" / stamp
            dst.mkdir(parents=True, exist_ok=True)
            old.rename(dst / old.name)
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
            how = draw(s, cand, s.get("seed") if rnd == 1 else None)
            _log(f"  [{i}/{len(pending)}] 镜头 {name}（{how}）画好，用时 {time.time() - t0:.0f}s")
            made.append((s, cand, time.time() - t0, how))

        if inspector.use_vision:  # 阶段 B：质检
            comfy.free()
        _log("  质检中 ...")
        still = []
        for s, cand, gen_sec, how in made:
            name = sbmod.shot_name(s)
            t0 = time.time()
            r = inspector.inspect(s, cand)
            r.update(round=rnd, gen_sec=round(gen_sec, 1), qa_sec=round(time.time() - t0, 1), mode=how)
            report[name]["attempts"].append(r)
            face_txt = " ".join(f"{k}={v}" + (f"(比对{r['face_ref'][k]})" if k in r.get("face_ref", {}) else "")
                                for k, v in r["face"].items())
            vis_txt = f" 画面={r['vision']['match_score']}/5" if "vision" in r else ""
            mark = "✅" if r["passed"] else "❌"
            _log(f"  {mark} 镜头 {name} {face_txt}{vis_txt} {'；'.join(r['notes'])}")
            if r["passed"]:
                shutil.copy(cand, img_dir / f"{name}.png")
                report[name]["final"] = cand.name
                inspector.remember(name, cand)
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
    angle_refs = {cid: [("定妆照", p)] + [(f"定妆照-{k}", out / "cast" / f"{cid}_{k}.png")
                                         for k in ANGLES if (out / "cast" / f"{cid}_{k}.png").exists()]
                  for cid, p in refs.items()}
    inspector = Inspector(cfg, sb, angle_refs, faces, use_vision=q.get("vision", True))
    report = {}
    for s in sb["shots"]:
        name = sbmod.shot_name(s)
        img = out / "images" / f"{name}.png"
        if not img.exists():
            continue
        r = inspector.inspect(s, img)
        report[name] = {"attempts": [r], "final": img.name}
        if r["passed"]:
            inspector.remember(name, img)
        mark = "✅" if r["passed"] else "❌"
        face_txt = " ".join(f"{k}={v}" + (f"(比对{r['face_ref'][k]})" if k in r.get("face_ref", {}) else "")
                                for k, v in r["face"].items())
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
        "| 镜头 | 方式 | 尝试次数 | 结果 | 人脸相似度 | 画面分 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    total_attempts, passed_first = 0, 0
    for name, r in report.items():
        atts = r["attempts"]
        total_attempts += len(atts)
        if atts and atts[0]["passed"]:
            passed_first += 1
        final = next((a for a in atts if Path(a["image"]).name == r.get("final")), atts[-1])
        def face_txt(k, v):
            if v is None:
                return f"{k}=无脸"
            pose = final.get("pose", {}).get(k, {})
            ref = final.get("face_ref", {}).get(k, "")
            extra = f"（比对 {ref}；脸高 {pose['face_h']:.0%}，转向 {pose['yaw']:+.2f}）" if pose else ""
            return f"{k}={v}{extra}"
        face = "<br>".join(face_txt(k, v) for k, v in final["face"].items()) or "-"
        vis = f"{final['vision']['match_score']}/5" if "vision" in final else "-"
        status = "⚠️ 未达标" if r.get("best_effort") else ("✅" if final["passed"] else "❌")
        lines.append(f"| {name} | {final.get('mode', '-')} | {len(atts)} | {status} | {face} | {vis} | {'；'.join(final['notes']) or '-'} |")
    n = len(report) or 1
    lines += ["", f"- 一次通过率：{passed_first}/{len(report)}（{passed_first / n:.0%}）",
              f"- 平均每个镜头出图次数：{total_attempts / n:.2f}"]
    if cross:
        lines += ["", "## 跨镜头一致性（同一角色所有镜头两两比对的最低相似度）", ""]
        lines += [f"- {cid}（{sb['characters'][cid].get('name', '')}）：{v}" for cid, v in cross.items()]
    (out / "qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"质检报告：{(out / 'qa_report.md').relative_to(ROOT)}")
