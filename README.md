# ai-tools · 本地 AI 短剧工作台

在 MacBook Pro（M5 Pro / 24G）上，用全本地、免费的 AI 模型做短剧素材：

```
剧本 ──(LM Studio)──▶ 分镜表 ──(ComfyUI)──▶ 分镜图
                         └──(Qwen3-TTS)──▶ 配音 ──▶ 字幕 + 预览视频 ──▶ 剪映精剪
```

## 目录结构

```
ai-tools/
├── README.md            ← 你在看的这个
├── config.toml          ← 所有配置：端口、模型、音色、字幕字数……
├── pyproject.toml       ← Python 依赖（用 uv 管理）
├── tts.py               ← 命令行 tts 命令用的配音脚本（独立使用）
├── studio/              ← 流水线源码
│   ├── cli.py           ← 命令入口：check / image / plan / run
│   ├── pipeline.py      ← 流水线主流程
│   ├── comfy.py         ← ComfyUI API 客户端（出图）
│   ├── tts.py           ← 配音（Qwen3-TTS / mlx-audio）
│   ├── subtitle.py      ← 由台词 + 音频时长生成 SRT
│   ├── video.py         ← ffmpeg 拼预览视频
│   ├── plan.py          ← LM Studio：剧本 → 分镜表
│   └── storyboard.py    ← 分镜表格式
├── workflows/           ← ComfyUI 工作流（API 格式）
├── screenplays/         ← 剧本（纯文本）
├── storyboards/         ← 分镜表（JSON）
├── output/              ← 生成结果，每个项目一个文件夹（不进 git）
└── docs/
    ├── toolbox.md       ← 各工具速查：LM Studio / ComfyUI / srt / tts
    ├── roadmap.md       ← 后续学习和扩展路线
    └── learning-log.md  ← 学习日志
```

## 第一次使用

```bash
cd ~/ai-tools
uv sync                 # 安装依赖（第一次要几分钟）
uv run studio check     # 检查环境
```

## 日常用法

```bash
# 1. 快速试一张图（调提示词用）
uv run studio image "Cinematic vertical film still of ..."

# 2. 跑示例：4 个镜头 → 图片、配音、字幕、预览视频
uv run studio run storyboards/example.json

# 3. 用自己的剧本：先让 LM Studio 拆分镜（要先打开 LM Studio 的 Server 并加载模型）
uv run studio plan screenplays/example.txt
#    打开 storyboards/example.json 检查修改，然后卸载 LM Studio 模型，再：
uv run studio run storyboards/example.json
```

结果在 `output/<标题>/`：

| 文件 | 说明 |
|---|---|
| `images/01.png …` | 每个镜头的分镜图 |
| `audio/01.wav …` | 每句台词的配音 |
| `subtitles.srt` | 字幕（每条 ≤16 字，可在 config.toml 调） |
| `preview.mp4` | 图片 + 配音 + 软字幕的预览视频，看节奏用 |
| `timeline.json` | 每个镜头的起止时间 |

**改一个镜头**：删掉对应的 `images/03.png` 或 `audio/03.wav`，再 `run` 一次，只会重新生成缺的文件。
**只跑某一步**：`uv run studio run storyboards/xxx.json --only audio`
**全部重来**：加 `--force`

## 注意内存（24G）

三个大模型不要同时开：**LM Studio 拆完分镜就卸载模型**；流水线出完图会自动让 ComfyUI 释放内存，再开始配音。

## 换出图模型

1. 在 ComfyUI 里调好新的工作流（比如 Z-Image Turbo），菜单 工作流 → 导出(API)，放进 `workflows/`
2. 打开这个 json，找到提示词、seed、保存图像这几个节点的编号，改 `config.toml` 的 `[comfy]` 部分
3. `uv run studio check` 确认节点都找得到
