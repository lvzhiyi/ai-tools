# 工具速查

## LM Studio（本地大模型）

- 用途：聊天、写剧本、拆分镜；给自己的程序提供 OpenAI 兼容接口
- 接口：`http://localhost:1234/v1`（Developer 页面打开 Server）
- 已下载：`google/gemma-4-e4b`（小、快、能看图）
- 24G 内存建议：日常 8B（8bit）/ 主力 14B（4bit）/ 尝鲜 30B-A3B MoE（4bit，需关掉其他应用）
- 代码里切换到本地模型：OpenAI SDK 的 `baseURL` 改成 `http://localhost:1234/v1`，apiKey 随便填

## ComfyUI（本地出图）

- 实例：`~/ComfyUI-Installs/comfy-lv`，端口 `8188`，硬件 Apple Silicon (MPS)
- 当前模型：MOSS-Image（5B，int8）+ Qwen3.5 9B 文本编码器 + Flux2 small VAE
- 实测：第一张约 4 分钟（含加载模型）
- **坑**：`enable_prompt_enhancer`（提示词扩写）在 Mac 上极慢（GPTQ 量化没有 Mac 加速），保持 false，提示词自己用英文写详细
- 竖屏：分辨率选择器选 `9:16 (Portrait Widescreen)`
- 导出 API 工作流：菜单 工作流 → 导出(API)

## srt（自动字幕，mlx-whisper）

```bash
srt 视频.mp4          # 在视频旁边生成同名 .srt
srt *.mp4             # 批量
```
- 模型：whisper-large-v3-turbo（约 1.6G，已下载）
- 用 `--initial-prompt "以下是普通话的句子。"` 避免繁体
- 每条字幕 ≤16 字、单行（`~/.zshrc` 里的 srt 函数）

## tts（配音，Qwen3-TTS）

```bash
tts "台词" -v Vivian -i "紧张，压低声音" -o 文件名
```
- 音色：Vivian（专业女声）/ Serena（讲故事女声）/ Uncle_Fu（旁白）/ Eric（四川话、活泼）/ Dylan（北京话）
- `-i` 用自然语言控制语气
- 声音克隆：换成 Base 模型 + 3 秒参考音频（见 roadmap）。**只克隆自己或已获得同意的声音**

## 常用路径

| 东西 | 位置 |
|---|---|
| 本项目 | `~/ai-tools` |
| ComfyUI 输出图片 | `~/ComfyUI-Installs/comfy-lv/output`（以实际为准） |
| LM Studio 模型 | `~/.lmstudio/models` |
| Hugging Face 模型缓存（whisper / tts） | `~/.cache/huggingface` |
| 国内下载镜像 | `HF_ENDPOINT=https://hf-mirror.com`（已写入 ~/.zshrc） |
