"""Robot voice for the quiz page: text -> mp3 via edge-tts, cached on disk.

Needs internet on the host (not on the TV). If synthesis fails the page just stays silent.
"""
import asyncio
import hashlib
import threading
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "tts"
VOICES = {"de": "de-DE-AmalaNeural", "zh": "zh-CN-XiaoyiNeural"}
MAX_CHARS = 400
_lock = threading.Lock()


def speak(text, lang="de", voice=None):
    """返回 mp3 字节；同一句话只合成一次。"""
    text = " ".join(text.split())[:MAX_CHARS]
    if not text:
        raise ValueError("empty text")
    voice = voice or VOICES.get(lang, VOICES["de"])
    path = CACHE / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest() + ".mp3")
    if path.exists():
        return path.read_bytes()
    import edge_tts  # 只有用到语音时才需要
    with _lock:  # 电视上快速连按时别并发去连语音服务
        if not path.exists():
            CACHE.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            # 稍快一点、音调高一点，更像小机器人
            asyncio.run(edge_tts.Communicate(text, voice, rate="+5%", pitch="+20Hz").save(str(tmp)))
            tmp.replace(path)
    return path.read_bytes()
