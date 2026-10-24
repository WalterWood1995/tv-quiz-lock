"""Build the reminder video: avatar + caption + spoken message, as an MP4 the TV can play."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
VIDEO = ASSETS / "reminder.mp4"
FONT = "C\\:/Windows/Fonts/msyhbd.ttc"

DEFAULT_TEXT = "你今天已经玩了一个小时了。眼睛需要休息，把游戏放下，去做点别的吧。"
DEFAULT_VOICE = "zh-CN-YunyangNeural"


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg")
    return exe


def build_video(text=DEFAULT_TEXT, voice=DEFAULT_VOICE, avatar=None, voice_file=None):
    """voice_file: 你自己录的声音（mp3/wav/m4a），有就用它，没有就用 TTS 合成 text。"""
    ASSETS.mkdir(exist_ok=True)
    audio = ASSETS / "reminder_voice.mp3"
    if voice_file:
        audio = Path(voice_file)
    else:
        asyncio.run(edge_tts.Communicate(text, voice).save(str(audio)))

    caption = ASSETS / "caption.txt"
    caption.write_text(text.replace("。", "。\n").strip(), encoding="utf-8")
    caption_arg = str(caption).replace("\\", "/").replace(":", "\\:")

    # Dark cyber background with a slow cyan glow; avatar in the middle, caption below
    bg = "color=c=0x05070f:s=1920x1080:r=25"
    glow = ("drawbox=x=0:y=0:w=iw:h=ih:color=0x00e5ff@0.06:t=fill,"
            "vignette=PI/4")
    cap = (f"drawtext=fontfile='{FONT}':textfile='{caption_arg}':fontsize=54:fontcolor=0xd8fbff:line_spacing=0:text_align=C:"
           "x=(w-text_w)/2:y=h-text_h-150:shadowcolor=0x00e5ff:shadowx=0:shadowy=0:borderw=2:bordercolor=0x00e5ff@0.5")
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", bg]
    if avatar and Path(avatar).exists():
        cmd += ["-loop", "1", "-i", str(avatar), "-i", str(audio)]
        graph = (f"[0]{glow}[bg];[1]scale=-2:640,format=rgba[av];"
                 f"[bg][av]overlay=(W-w)/2:110:shortest=1,{cap}[v]")
    else:
        # No photo yet: a neon placeholder badge
        cmd += ["-i", str(audio)]
        graph = (f"[0]{glow},drawbox=x=(iw-520)/2:y=120:w=520:h=520:color=0x00e5ff@0.8:t=6,"
                 f"drawtext=fontfile='{FONT}':text='家长':fontsize=200:fontcolor=0x00e5ff:"
                 f"x=(w-text_w)/2:y=280,{cap}[v]")
    audio_idx = 2 if avatar and Path(avatar).exists() else 1
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", f"{audio_idx}:a",
            "-af", "apad=pad_dur=3", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(VIDEO)]
    subprocess.run(cmd, check=True)
    return VIDEO
