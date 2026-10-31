"""人工标注：两张图并排，你判断"像不像同一个人"，结果存到 output/<项目>/labels.json。

为什么要人工标注：提示词写的是"林夏"，不代表画出来的就是同一个人。
"观众觉得是不是同一个角色"才是最终标准，所以用人眼的判断作为阈值校准的正确答案。

刻意的设计：
- 盲标：页面不显示 SFace 分数，避免被数字影响
- 打乱顺序：避免前面的图影响后面的判断
- 每点一次就存盘，随时可以中断，下次接着标
"""
from __future__ import annotations

import itertools
import json
import random
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import storyboard as sbmod
from .config import ROOT


def collect_pairs(sb: dict, out: Path) -> list[dict]:
    """每个角色：定妆照 + 所有包含它的镜头候选图，两两组成一对。"""
    cand = out / "images" / "_candidates"
    pairs = []
    for cid in sb["characters"]:
        imgs = []
        cast = out / "cast" / f"{cid}.png"
        if cast.exists():
            imgs.append(cast)
        for s in sb["shots"]:
            if cid in s["characters"]:
                imgs += sorted(cand.glob(f"{sbmod.shot_name(s)}_*.png"))
        for a, b in itertools.combinations(imgs, 2):
            ra, rb = str(a.relative_to(out)), str(b.relative_to(out))
            pairs.append({"key": f"{ra}|{rb}", "a": ra, "b": rb, "character": cid})
    random.Random(42).shuffle(pairs)
    return pairs


def load_labels(out: Path) -> dict:
    p = out / "labels.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>人脸标注</title>
<style>
 body{margin:0;background:#141418;color:#eee;font-family:-apple-system,"PingFang SC",sans-serif}
 header{padding:14px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #2a2a30}
 .q{font-size:18px} .q b{color:#ffcf5a} .prog{color:#999}
 .pair{display:flex;gap:20px;justify-content:center;padding:18px}
 .pair figure{margin:0;text-align:center} .pair img{height:calc(100vh - 230px);border-radius:8px}
 figcaption{color:#888;font-size:13px;margin-top:6px}
 .btns{display:flex;gap:14px;justify-content:center;padding-bottom:16px}
 button{font-size:17px;padding:12px 26px;border-radius:10px;border:0;cursor:pointer;color:#fff}
 .same{background:#2a9d4b}.diff{background:#c94a4a}.skip{background:#555}.back{background:#333}
 button.on{outline:3px solid #ffcf5a}
 .done{text-align:center;padding-top:30vh;font-size:22px}
</style></head><body>
<header><div class="q">如果这两张出现在同一部剧里，你会觉得是<b>同一个角色</b>吗？<span style="color:#888;font-size:14px">（只看脸，衣服发型本来就一样）</span></div>
<div class="prog" id="prog"></div></header>
<div id="main"></div>
<script>
const PAIRS = __PAIRS__; let labels = __LABELS__;
let i = PAIRS.findIndex(p => !labels[p.key]); if (i < 0) i = PAIRS.length;
const name = s => s.split('/').pop();
function render(){
  const done = PAIRS.filter(p => labels[p.key]).length;
  document.getElementById('prog').textContent = `已标 ${done} / ${PAIRS.length}　快捷键：1 像　2 不像　3 说不准　← 上一对`;
  const m = document.getElementById('main');
  if (i >= PAIRS.length){ m.innerHTML = `<div class="done">✅ 全部标完（${done} 对）<br><br><span style="font-size:16px;color:#999">回到终端按 Ctrl+C 结束，然后运行 studio calibrate</span><br><br><button class="back" onclick="i=PAIRS.length-1;render()">← 回看上一对</button></div>`; return; }
  const p = PAIRS[i], cur = labels[p.key];
  m.innerHTML = `<div class="pair"><figure><img src="/img?p=${encodeURIComponent(p.a)}"><figcaption>${name(p.a)}</figcaption></figure>
    <figure><img src="/img?p=${encodeURIComponent(p.b)}"><figcaption>${name(p.b)}</figcaption></figure></div>
    <div class="btns"><button class="back" onclick="go(-1)">← 上一对</button>
    <button class="same ${cur=='same'?'on':''}" onclick="mark('same')">1 · 像，同一个人</button>
    <button class="diff ${cur=='diff'?'on':''}" onclick="mark('diff')">2 · 不像</button>
    <button class="skip ${cur=='skip'?'on':''}" onclick="mark('skip')">3 · 说不准</button></div>`;
}
function go(d){ i = Math.max(0, Math.min(PAIRS.length, i + d)); render(); }
async function mark(v){
  const p = PAIRS[i]; labels[p.key] = v;
  await fetch('/label', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key:p.key, value:v})});
  go(1);
}
document.addEventListener('keydown', e => {
  if (e.key==='1') mark('same'); else if (e.key==='2') mark('diff'); else if (e.key==='3') mark('skip');
  else if (e.key==='ArrowLeft') go(-1); else if (e.key==='ArrowRight') go(1);
});
render();
</script></body></html>"""


def serve(sb: dict, out: Path, port: int = 8765, open_browser: bool = True) -> None:
    pairs = collect_pairs(sb, out)
    if not pairs:
        raise RuntimeError("没有可标注的图。先跑一次带质检的 run，生成定妆照和候选图")
    labels_path = out / "labels.json"
    labels = load_labels(out)
    lock = threading.Lock()
    root = out.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                html = PAGE.replace("__PAIRS__", json.dumps(pairs, ensure_ascii=False)) \
                           .replace("__LABELS__", json.dumps(labels, ensure_ascii=False))
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if u.path == "/img":
                p = (root / parse_qs(u.query).get("p", [""])[0]).resolve()
                if root not in p.parents or not p.is_file():  # 只允许读项目目录里的图
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, p.read_bytes(), "image/png")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/label":
                return self._send(404, b"", "text/plain")
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if data.get("value") not in ("same", "diff", "skip"):
                return self._send(400, b"bad value", "text/plain")
            with lock:
                labels[data["key"]] = data["value"]
                labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
            self._send(200, b"{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    done = sum(1 for p in pairs if p["key"] in labels)
    print(f"共 {len(pairs)} 对，已标 {done} 对。浏览器打开：{url}")
    print(f"每点一次自动保存到 {labels_path.relative_to(ROOT)}；标完回到这里按 Ctrl+C 结束")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        labels = load_labels(out)
        n = {v: sum(1 for x in labels.values() if x == v) for v in ("same", "diff", "skip")}
        print(f"\n已保存：像 {n['same']} 对 · 不像 {n['diff']} 对 · 说不准 {n['skip']} 对")
