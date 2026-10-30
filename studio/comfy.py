"""ComfyUI API 客户端。

ComfyUI 的接口很简单：
  POST /prompt          提交一个（API 格式的）工作流，返回 prompt_id
  GET  /history/<id>    任务完成后能查到输出图片的文件名
  GET  /view?...        下载图片
  POST /free            卸载模型、释放内存
"""
from __future__ import annotations

import copy
import json
import struct
import zlib
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import ROOT


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = cfg["url"].rstrip("/")
        self.client_id = str(uuid.uuid4())
        wf_path = ROOT / cfg["workflow"]
        with open(wf_path, encoding="utf-8") as f:
            self.workflow = json.load(f)

    # ---------- HTTP ----------
    def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise ComfyError(f"ComfyUI 返回 {e.code}：{detail[:800]}") from e
        except urllib.error.URLError as e:
            raise ComfyError(f"连不上 ComfyUI（{self.url}），请确认它已经打开。{e.reason}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def ping(self) -> dict:
        return self._request("GET", "/system_stats", timeout=5)

    def free(self) -> None:
        """出完图后卸载模型，把内存让给配音模型。"""
        try:
            self._request("POST", "/free", {"unload_models": True, "free_memory": True})
        except ComfyError:
            pass

    # ---------- 工作流 ----------
    def check_workflow(self) -> list[str]:
        problems = []
        for key in ("prompt_node", "seed_node", "save_node"):
            node = self.cfg.get(key)
            if node not in self.workflow:
                problems.append(f"config.toml 里 {key} = {node!r}，但工作流里没有这个节点")
        return problems

    def _build(self, prompt: str, seed: int | None) -> dict:
        wf = copy.deepcopy(self.workflow)
        node = wf[self.cfg["prompt_node"]]["inputs"]
        for key in ("value", "text", "prompt"):
            if key in node and isinstance(node[key], str):
                node[key] = prompt
                break
        else:
            raise ComfyError(f"提示词节点 {self.cfg['prompt_node']} 没有可填的文本输入")

        wf[self.cfg["seed_node"]]["inputs"]["seed"] = seed if seed is not None else random.randint(0, 2**48)

        res_node = self.cfg.get("resolution_node")
        if res_node in wf and "aspect_ratio" in wf[res_node]["inputs"] and self.cfg.get("aspect_ratio"):
            wf[res_node]["inputs"]["aspect_ratio"] = self.cfg["aspect_ratio"]
        return wf

    def generate(self, prompt: str, out_path: Path, seed: int | None = None, retries: int = 1) -> Path:
        """出一张图并保存到 out_path；如果是纯黑图（数值算崩了）就换 seed 重画。"""
        for attempt in range(retries + 1):
            path = self._generate_once(prompt, out_path, seed if attempt == 0 else None)
            if not is_black_png(path.read_bytes()):
                return path
            print(f"  ⚠️ 出了一张纯黑图（第 {attempt + 1} 次），换个 seed 重画", flush=True)
        path.unlink(missing_ok=True)
        raise ComfyError("连续出纯黑图：通常是尺寸不合适或内存不足，检查分辨率是否为 64 的倍数、关掉 LM Studio 再试")

    def _generate_once(self, prompt: str, out_path: Path, seed: int | None) -> Path:
        wf = self._build(prompt, seed)
        res = self._request("POST", "/prompt", {"prompt": wf, "client_id": self.client_id})
        if not isinstance(res, dict) or "prompt_id" not in res:
            raise ComfyError(f"提交任务失败：{res}")
        if res.get("node_errors"):
            raise ComfyError(f"工作流节点报错：{json.dumps(res['node_errors'], ensure_ascii=False)[:800]}")
        pid = res["prompt_id"]

        deadline = time.time() + float(self.cfg.get("timeout_sec", 1200))
        while time.time() < deadline:
            hist = self._request("GET", f"/history/{pid}")
            if isinstance(hist, dict) and pid in hist:
                item = hist[pid]
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    raise ComfyError(f"出图失败：{json.dumps(msgs, ensure_ascii=False)[:800]}")
                if status.get("completed", True):
                    return self._download(item, out_path)
            time.sleep(2)
        raise ComfyError(f"等待超时（{self.cfg.get('timeout_sec')} 秒），可在 config.toml 调大 timeout_sec")

    def _download(self, item: dict, out_path: Path) -> Path:
        outputs = item.get("outputs", {})
        images = outputs.get(self.cfg["save_node"], {}).get("images")
        if not images:  # 兜底：找任意一个有图片输出的节点
            images = next((o["images"] for o in outputs.values() if o.get("images")), None)
        if not images:
            raise ComfyError("任务完成了，但没有找到输出图片")
        img = images[0]
        query = urllib.parse.urlencode(
            {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
        )
        with urllib.request.urlopen(f"{self.url}/view?{query}", timeout=60) as resp:
            data = resp.read()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return out_path


def is_black_png(data: bytes) -> bool:
    """不依赖图像库，判断 PNG 是否（几乎）全黑：解压像素数据看最大值。"""
    if not data.startswith(b"\x89PNG"):
        return False
    pos, idat = 8, b""
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        if ctype == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return False
    # 纯黑图经过 PNG 行过滤后只剩 0 和过滤类型字节（0~4）
    return max(raw, default=0) <= 4
