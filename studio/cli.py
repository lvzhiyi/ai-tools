"""命令行入口：uv run studio <命令>

  studio check                         检查环境（ComfyUI / LM Studio / ffmpeg / 配音模型）
  studio image "画面描述"               只出一张图，快速试提示词
  studio plan 剧本.txt                  本地优先拆分镜，可选云端兜底 → storyboards/
  studio run storyboards/xxx.json      跑完整流水线 → output/（出图带 AI 质检）
  studio i2v storyboards/xxx.json      图生视频：每个镜头的图 → 几秒动态镜头（LTX-2.3，本地 MLX）
  studio cast storyboards/xxx.json     只生成角色定妆照（质检用的"标准脸"）
  studio runs                          查看运行历史：每次的耗时、通过率，和上一次对比
  studio web                           打开网页控制台（http://127.0.0.1:8780）：编辑分镜、跑任务、看结果
  studio qa storyboards/xxx.json       只质检已有的图，不重画，出报告
  studio label storyboards/xxx.json    人工标注"像不像同一个人"（校准用的正确答案）
  studio calibrate storyboards/xxx.json  用人工标注 + 对照组校准人脸相似度阈值
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import ROOT, load_config


def cmd_check(cfg: dict, _args) -> int:
    from .checks import run

    icon = {"ok": "✅", "warn": "⚪", "fail": "❌"}
    items = run(cfg)
    for it in items:
        print(f"{icon[it['status']]} {it['name']}：{it['detail']}")
    return 1 if any(it["status"] == "fail" for it in items) else 0


def cmd_image(cfg: dict, args) -> int:
    from .comfy import ComfyClient

    out = ROOT / "output" / "_quick" / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
    t0 = time.time()
    ComfyClient(cfg["comfy"]).generate(args.prompt, out, seed=args.seed)
    print(f"✅ {out}（用时 {time.time() - t0:.0f}s）")
    return 0


def cmd_plan(cfg: dict, args) -> int:
    from .plan import plan

    script = Path(args.script).read_text(encoding="utf-8")
    mode_name = {"local": "仅本地", "cloud": "仅云端", "auto": "本地优先，云端兜底"}[cfg["lmstudio"].get("routing_mode", "auto")]
    print(f"正在拆分镜（{mode_name}）...")
    sb = plan(script, cfg["lmstudio"], max_shots=args.max_shots)
    out = ROOT / "storyboards" / f"{Path(args.script).stem}.json"
    out.write_text(json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 分镜表：{out}（{len(sb.get('shots', []))} 个镜头）")
    print("   先打开检查、修改，再运行：uv run studio run " + str(out.relative_to(ROOT)))
    print("   提示：出图前记得在 LM Studio 里卸载模型，把内存让给 ComfyUI")
    return 0


def _load_sb(path: str):
    from . import storyboard as sbmod
    from .pipeline import project_dir

    sb = sbmod.load(Path(path))
    out = project_dir(sb)
    out.mkdir(parents=True, exist_ok=True)
    return sb, out


def cmd_cast(cfg: dict, args) -> int:
    from .comfy import ComfyClient, make_editor
    from .face import FaceMatcher
    from .qa import cast_angles, ensure_cast

    sb, out = _load_sb(args.storyboard)
    if not sb["characters"]:
        print("分镜表里没有定义 characters，不需要定妆照")
        return 0
    comfy = ComfyClient(cfg["comfy"])
    comfy.ping()
    faces = FaceMatcher()
    front = ensure_cast(sb, out, comfy, faces, force=args.force)
    editor = make_editor(cfg["comfy"]) if cfg.get("qa", {}).get("cast_angles", True) else None
    refs = cast_angles(sb, out, front, editor, force=args.force, faces=faces,
                       threshold=float(cfg.get("qa", {}).get("angle_threshold", 0.35)))
    for cid, items in refs.items():
        for label, p in items:
            print(f"✅ {cid} {label}：{p}")
    print("满意就保留；不满意加 --force 重画，或在分镜表里给角色指定 ref（自己挑的图）")
    return 0


def cmd_qa(cfg: dict, args) -> int:
    from .comfy import ComfyClient, ComfyError
    from .qa import inspect_existing

    sb, out = _load_sb(args.storyboard)
    if args.no_vision:
        cfg.setdefault("qa", {})["vision"] = False
    comfy = ComfyClient(cfg["comfy"])
    try:
        comfy.ping()
    except ComfyError:
        comfy = None  # 定妆照都已存在时不需要 ComfyUI
    inspect_existing(sb, out, cfg, comfy)
    return 0


def cmd_label(cfg: dict, args) -> int:
    from .label import serve

    sb, out = _load_sb(args.storyboard)
    serve(sb, out, port=args.port)
    return 0


def cmd_calibrate(cfg: dict, args) -> int:
    from .calibrate import run as calibrate
    from .comfy import ComfyClient, ComfyError

    sb, out = _load_sb(args.storyboard)
    comfy = ComfyClient(cfg["comfy"])
    try:
        comfy.ping()
    except ComfyError:
        comfy = None  # 对照组都生成过了就不需要 ComfyUI
    r = calibrate(sb, out, cfg, comfy, people=args.people)
    s, d = r["same"], r["diff"]
    print()
    print(f"同一个人：{s['n']} 对，相似度 {s['min']:.2f} ~ {s['max']:.2f}（中位 {s['median']:.2f}）")
    print(f"不同的人：{d['n']} 对，相似度 {d['min']:.2f} ~ {d['max']:.2f}（中位 {d['median']:.2f}）")
    print(f"结论：{r['verdict']}")
    cur = cfg.get("qa", {}).get("face_threshold", 0.40)
    if r["separable"]:
        print(f"建议阈值：{r['suggested']}（当前 config.toml 里是 {cur}）")
    else:
        print(f"建议阈值：不可靠（两组重叠太多），先别改 config.toml（当前 {cur}）")
    for c in r["compare"]:
        agree = f"   和你的判断一致 {c['agreement']:>4.0%}" if "agreement" in c else ""
        print(f"  阈值 {c['threshold']:<6} 误拒 {c['false_reject_rate']:>4.0%}   误收 {c['false_accept_rate']:>4.0%}{agree}")
    return 0


def cmd_runs(cfg: dict, args) -> int:
    from .metrics import read_history

    rows = read_history(args.project)[-args.n:]
    if not rows:
        print("还没有运行记录（每次 studio run 会自动记录到 output/runs.csv）")
        return 0
    f = lambda v: "-" if v in ("", None) else (f"{float(v) / 60:.1f}m" if float(v) >= 90 else f"{float(v):.0f}s")  # noqa: E731
    print(f"{'时间':<17} {'项目':<10} {'git':<12} {'状态':<4} {'文生图':<10} {'总耗时':>6} {'出图':>6} {'配音':>6} {'每张图':>6} {'一次通过':>6}")
    for r in rows:
        print(f"{r['time'][5:16]:<17} {r['project'][:10]:<10} {r['git'][:12]:<12} {'✅' if r['status'] == 'ok' else '❌':<4} "
              f"{r['t2i'][:10]:<10} {f(r['total_sec']):>6} {f(r['images_sec']):>6} {f(r['audio_sec']):>6} "
              f"{f(r['avg_image_sec']):>6} {r['first_pass']:>6}")
    print("\n每次运行的详细报告：output/<项目>/runs/<时间>/report.md")
    return 0


def cmd_run(cfg: dict, args) -> int:
    from .pipeline import run

    if args.i2v:
        cfg.setdefault("i2v", {})["enabled"] = True
    run(Path(args.storyboard), cfg, only=args.only, force=args.force,
        command="studio " + " ".join(sys.argv[1:]))
    return 0


def cmd_i2v(cfg: dict, args) -> int:
    """只跑图生视频（图和配音要先有），然后重新拼预览视频。"""
    from .pipeline import run

    shots = {x.strip() for x in args.shots.split(",")} if args.shots else None
    if args.seed is not None:
        cfg.setdefault("i2v", {})["seed"] = args.seed
    command = "studio " + " ".join(sys.argv[1:])
    run(Path(args.storyboard), cfg, only="i2v", force=args.force, command=command, shots=shots)
    run(Path(args.storyboard), cfg, only="video", command=command + " (video)")
    return 0


def cmd_web(cfg: dict, args) -> int:
    from .web.app import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="studio", description="本地 AI 短剧生产线")
    p.add_argument("--llm-mode", choices=["local", "cloud", "auto"], help="拆分镜/看图：仅本地、仅云端或自动兜底")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="检查环境")

    s = sub.add_parser("image", help="出一张图")
    s.add_argument("prompt")
    s.add_argument("--seed", type=int)

    s = sub.add_parser("plan", help="剧本 → 分镜表（本地优先，可选云端兜底）")
    s.add_argument("script", help="剧本文本文件")
    s.add_argument("--max-shots", type=int, default=12)

    s = sub.add_parser("run", help="分镜表 → 图片/配音/字幕/预览视频")
    s.add_argument("storyboard")
    s.add_argument("--only", choices=["images", "audio", "i2v", "subtitle", "video"], help="只跑某一步")
    s.add_argument("--i2v", action="store_true", help="加上图生视频（默认关，较慢）")
    s.add_argument("--force", action="store_true", help="已有文件也重新生成")
    s.add_argument("--no-qa", action="store_true", help="关闭 AI 质检（只画一次）")
    s.add_argument("--no-edit", action="store_true", help="不用参考图编辑，全部文生图（做对比实验用）")

    s = sub.add_parser("i2v", help="图生视频：镜头图 → 动态镜头，并重拼预览视频")
    s.add_argument("storyboard")
    s.add_argument("--shots", help="只做某几个镜头，如 2,4 或 02,04")
    s.add_argument("--force", action="store_true", help="已有片段也重新生成")
    s.add_argument("--seed", type=int, help="换一个随机种子（同样的图和描述，换种子才会生成不同的动作）")

    s = sub.add_parser("web", help="网页控制台")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--host", default="127.0.0.1", help="只给本机用；局域网访问改成 0.0.0.0")
    s.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    s = sub.add_parser("runs", help="查看运行历史（耗时、通过率）")
    s.add_argument("--project", help="只看某个项目（分镜表里的 title）")
    s.add_argument("-n", type=int, default=15, help="显示最近几次")

    s = sub.add_parser("cast", help="生成角色定妆照")
    s.add_argument("storyboard")
    s.add_argument("--force", action="store_true", help="重画定妆照")

    s = sub.add_parser("qa", help="质检已有的图（不重画）")
    s.add_argument("storyboard")
    s.add_argument("--no-vision", action="store_true", help="只做人脸比对，不用看图模型")

    s = sub.add_parser("label", help="人工标注：两张图像不像同一个人")
    s.add_argument("storyboard")
    s.add_argument("--port", type=int, default=8765)

    s = sub.add_parser("calibrate", help="校准人脸相似度阈值")
    s.add_argument("storyboard")
    s.add_argument("--people", type=int, default=6, help="对照组人数（每人约 4 分钟）")

    args = p.parse_args(argv)
    cfg = load_config()
    if args.llm_mode:
        cfg["lmstudio"]["routing_mode"] = args.llm_mode
        cfg["lmstudio"].setdefault("cloud", {})["enabled"] = args.llm_mode != "local"
    if getattr(args, "no_qa", False):
        cfg.setdefault("qa", {})["enabled"] = False
    if getattr(args, "no_edit", False) and isinstance(cfg["comfy"].get("edit"), dict):
        cfg["comfy"]["edit"]["enabled"] = False
    handler = {"check": cmd_check, "image": cmd_image, "plan": cmd_plan, "run": cmd_run,
               "cast": cmd_cast, "qa": cmd_qa, "calibrate": cmd_calibrate, "label": cmd_label, "runs": cmd_runs, "i2v": cmd_i2v, "web": cmd_web}[args.cmd]
    try:
        return handler(cfg, args)
    except KeyboardInterrupt:
        print("\n已中断。已生成的文件会保留，下次运行自动跳过。")
        return 130
    except Exception as e:  # 给出可读的错误，而不是一大段堆栈
        print(f"\n❌ {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
