# ai-tools 的命令行小工具。在 ~/.zshrc 里加一行即可启用：
#   source ~/ai-tools/shell/aliases.zsh

# 国内下载 Hugging Face 模型走镜像
export HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}

# srt：给已有视频/音频自动生成字幕（mlx-whisper，large-v3-turbo）
#   用法：srt 视频.mp4        批量：srt *.mp4
#   每条字幕单行、不超过 16 字；--initial-prompt 让输出为简体中文
srt() {
  for f in "$@"; do
    mlx_whisper "$f" --model mlx-community/whisper-large-v3-turbo \
      --language zh --initial-prompt "以下是普通话的句子。" \
      --word-timestamps True --max-line-width 16 --max-line-count 1 \
      --output-format srt --output-dir "$(dirname "$f")"
  done
}

# tts：单句配音（Qwen3-TTS）
#   用法：tts "台词" -v Vivian -i "紧张，压低声音" -o 文件名
tts() { uv run --python 3.12 --with mlx-audio python ~/ai-tools/tts.py "$@"; }
