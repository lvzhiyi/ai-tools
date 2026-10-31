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
    def __init__(self, cfg: dict, profile: str | None = None):
        """cfg 是 config.toml 的 [comfy] 部分。
        profile 选用哪套工作流：不传 = 文生图（由 t2i_profile 决定是 klein 还是 MOSS）；"edit" = 参考图编辑。
        """
        profile = profile or cfg.get("t2i_profile", "t2i")
        sub = cfg.get(profile)
        if sub is None and "workflow" in cfg:  # 兼容旧配置：工作流参数直接写在 [comfy] 下
            sub = cfg
        if sub is None:
            raise ComfyError(f"config.toml 里没有 [comfy.{profile}] 配置")
        self.profile = profile
        self.cfg = {**{k: v for k, v in cfg.items() if not isinstance(v, dict)}, **sub}
        self.url = self.cfg["url"].rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._uploaded: dict[str, str] = {}
        wf_path = ROOT / self.cfg["workflow"]
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
        keys = ["prompt_node", "seed_node", "save_node"] + (["image_node"] if self.profile == "edit" else [])
        if not (ROOT / self.cfg["workflow"]).exists():
            return [f"工作流文件不存在：{self.cfg['workflow']}"]
        for key in keys:
            node = self.cfg.get(key)
            if node not in self.workflow:
                problems.append(f"config.toml 里 {key} = {node!r}，但工作流里没有这个节点")
        return problems

    def _build(self, prompt: str, seed: int | None, ref_name: str | None = None) -> dict:
        wf = copy.deepcopy(self.workflow)
        node = wf[self.cfg["prompt_node"]]["inputs"]
        for key in ("value", "text", "prompt"):
            if key in node and isinstance(node[key], str):
                node[key] = prompt
                break
        else:
            raise ComfyError(f"提示词节点 {self.cfg['prompt_node']} 没有可填的文本输入")

        seed_inputs = wf[self.cfg["seed_node"]]["inputs"]
        seed_key = next((k for k in ("seed", "noise_seed") if k in seed_inputs), "seed")  # KSampler / RandomNoise
        seed_inputs[seed_key] = seed if seed is not None else random.randint(0, 2**48)

        res_node = self.cfg.get("resolution_node")
        if res_node in wf and "aspect_ratio" in wf[res_node]["inputs"] and self.cfg.get("aspect_ratio"):
            wf[res_node]["inputs"]["aspect_ratio"] = self.cfg["aspect_ratio"]

        if self.profile == "edit":
            if not ref_name:
                raise ComfyError("编辑模式需要参考图")
            wf[self.cfg["image_node"]]["inputs"]["image"] = ref_name
        return wf

    def upload_image(self, path: Path) -> str:
        """把参考图上传到 ComfyUI 的 input 目录，返回 LoadImage 节点要填的文件名（同一张图只传一次）。"""
        key = f"{path.resolve()}:{path.stat().st_mtime_ns}"
        if key in self._uploaded:
            return self._uploaded[key]
        boundary = uuid.uuid4().hex
        name = f"ai-tools_{path.stem}_{path.stat().st_mtime_ns % 10**8}{path.suffix}"
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n".encode(), path.read_bytes(), b"\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ])
        req = urllib.request.Request(self.url + "/upload/image", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
        except urllib.error.URLError as e:
            raise ComfyError(f"上传参考图失败：{e}") from e
        uploaded = f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]
        self._uploaded[key] = uploaded
        return uploaded

    def generate(self, prompt: str, out_path: Path, seed: int | None = None, retries: int = 1,
                 ref_image: Path | None = None) -> Path:
        """出一张图并保存到 out_path；ref_image 是参考图（编辑模式）。纯黑图（数值算崩了）会换 seed 重画。"""
        ref_name = self.upload_image(ref_image) if ref_image else None
        for attempt in range(retries + 1):
            path = self._generate_once(prompt, out_path, seed if attempt == 0 else None, ref_name)
            if not is_black_png(path.read_bytes()):
                return path
            print(f"  ⚠️ 出了一张纯黑图（第 {attempt + 1} 次），换个 seed 重画", flush=True)
        path.unlink(missing_ok=True)
        raise ComfyError("连续出纯黑图：通常是尺寸不合适或内存不足，检查分辨率是否为 64 的倍数、关掉 LM Studio 再试")

    def _generate_once(self, prompt: str, out_path: Path, seed: int | None, ref_name: str | None = None) -> Path:
        wf = self._build(prompt, seed, ref_name)
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


def make_editor(cfg: dict) -> "ComfyClient | None":
    """参考图编辑客户端：config.toml 里有 [comfy.edit] 且 enabled = true 才启用。"""
    edit = cfg.get("edit")
    if not edit or not edit.get("enabled", True):
        return None
    return ComfyClient(cfg, profile="edit")


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
