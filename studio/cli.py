"""命令行入口：uv run studio <命令>

  studio check                         检查环境（ComfyUI / LM Studio / ffmpeg / 配音模型）
  studio image "画面描述"               只出一张图，快速试提示词
  studio plan 剧本.txt                  用 LM Studio 把剧本拆成分镜表 → storyboards/
  studio run storyboards/xxx.json      跑完整流水线 → output/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import ROOT, load_config


def cmd_check(cfg: dict, _args) -> int:
    from .comfy import ComfyClient, ComfyError
    from .video import has_ffmpeg

    ok = True
    try:
        c = ComfyClient(cfg["comfy"])
        stats = c.ping()
        dev = (stats or {}).get("devices", [{}])[0].get("type", "?")
        print(f"✅ ComfyUI 已连接（{cfg['comfy']['url']}，设备 {dev}）")
        for p in c.check_workflow():
            ok = False
            print(f"❌ {p}")
    except ComfyError as e:
        ok = False
        print(f"❌ {e}")

    import urllib.request
    try:
        with urllib.request.urlopen(cfg["lmstudio"]["url"].rstrip("/") + "/models", timeout=3) as r:
            models = [m["id"] for m in json.load(r).get("data", [])]
        print(f"✅ LM Studio 已连接，可用模型：{', '.join(models) or '（无）'}")
    except Exception:
        print("⚪ LM Studio 没开（只有 studio plan 需要它）")

    print("✅ ffmpeg 已安装" if has_ffmpeg() else "❌ 没有 ffmpeg：brew install ffmpeg")
    try:
        import mlx_audio  # noqa: F401
        print("✅ mlx-audio 已安装（配音可用）")
    except ImportError:
        ok = False
        print("❌ mlx-audio 没装好：在项目目录运行 uv sync")
    return 0 if ok else 1


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
    print("正在用 LM Studio 拆分镜（小模型可能要一两分钟）...")
    sb = plan(script, cfg["lmstudio"], max_shots=args.max_shots)
    out = ROOT / "storyboards" / f"{Path(args.script).stem}.json"
    out.write_text(json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 分镜表：{out}（{len(sb.get('shots', []))} 个镜头）")
    print("   先打开检查、修改，再运行：uv run studio run " + str(out.relative_to(ROOT)))
    print("   提示：出图前记得在 LM Studio 里卸载模型，把内存让给 ComfyUI")
    return 0


def cmd_run(cfg: dict, args) -> int:
    from .pipeline import run

    run(Path(args.storyboard), cfg, only=args.only, force=args.force)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="studio", description="本地 AI 短剧生产线")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="检查环境")

    s = sub.add_parser("image", help="出一张图")
    s.add_argument("prompt")
    s.add_argument("--seed", type=int)

    s = sub.add_parser("plan", help="剧本 → 分镜表（需要 LM Studio）")
    s.add_argument("script", help="剧本文本文件")
    s.add_argument("--max-shots", type=int, default=12)

    s = sub.add_parser("run", help="分镜表 → 图片/配音/字幕/预览视频")
    s.add_argument("storyboard")
    s.add_argument("--only", choices=["images", "audio", "subtitle", "video"], help="只跑某一步")
    s.add_argument("--force", action="store_true", help="已有文件也重新生成")

    args = p.parse_args(argv)
    cfg = load_config()
    handler = {"check": cmd_check, "image": cmd_image, "plan": cmd_plan, "run": cmd_run}[args.cmd]
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
