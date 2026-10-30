import argparse
from mlx_audio.tts.generate import generate_audio
p = argparse.ArgumentParser(description="本地中文配音")
p.add_argument("text", help="要读的台词")
p.add_argument("-v", "--voice", default="Vivian", help="音色：Vivian / Serena / Uncle_Fu / Eric / Dylan")
p.add_argument("-i", "--instruct", default=None, help="语气，如：紧张、压低声音")
p.add_argument("-o", "--out", default="tts_output", help="输出文件名")
a = p.parse_args()
generate_audio(
    text=a.text,
    model="mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
    voice=a.voice,
    lang_code="chinese",
    instruct=a.instruct,
    file_prefix=a.out,
    audio_format="wav",
    join_audio=True,
    verbose=True,
)
