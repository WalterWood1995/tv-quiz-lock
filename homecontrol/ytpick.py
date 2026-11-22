"""在 YouTube 上找科普短视频，给答题中间「必看视频」用。

不用 API key：直接读 YouTube 搜索页里的 ytInitialData。为了安全，只要白名单里的
儿童科普频道的视频（[quiz] video_channels 可以改），时长 30 秒到 4 分钟。
搜到的结果存在 data/video_pool.json，每天最多刷新一次；搜不到时用 assets/videos.json。
"""
import json
import random
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "video_pool.json"
CURATED = ROOT / "assets" / "videos.json"

TOPICS = ["Weltall", "Planeten", "Mond", "Sonne", "Pyramiden", "Ägypten", "Römer", "Ritter", "Brücke",
          "Flugzeug", "Rakete", "Roboter", "Vulkan", "Dinosaurier", "Erdbeben", "Tiefsee", "Strom", "Magnet",
          "Experiment", "Körper", "Gewitter", "Tiere", "space", "pyramids", "engineering", "volcano",
          "dinosaurs", "planets", "robots", "ocean", "inventions"]

# 搜索时拿「频道名 + 话题」去搜，命中率比只搜话题高很多
SEARCH_CHANNELS = ["Woozle Goozle", "Terra X Kids", "SciShow Kids", "Peekaboo Kidz", "Kurzgesagt",
                   "Terra X", "planet schule", "TED-Ed", "Nat Geo Kids", "Mark Rober"]

# 儿童/青少年科普频道（频道名包含这些字样就算，不分大小写）
CHANNELS = [
    "Woozle Goozle", "Checker Tobi", "Die Sendung mit der Maus", "Wissen macht Ah", "planet schule",
    "Terra X", "Kurzgesagt", "logo!", "PUR+", "Löwenzahn", "neuneinhalb", "Clever", "SimpleClub",
    "TED-Ed", "SciShow Kids", "National Geographic Kids", "Nat Geo Kids", "Peekaboo Kidz", "Free School",
    "Mark Rober", "Kids Learning Tube", "Crash Course Kids", "BBC Earth", "NASA", "ESA", "DLR",
    "Doktor Whatson", "Breaking Lab", "Lesch", "MrWissen2go", "小灯塔", "宝宝巴士", "Smile and Learn",
]

SHORT_FILTER = "EgIYAQ%253D%253D"  # 搜索筛选：时长不到 4 分钟


def _seconds(text):
    parts = [int(p) for p in (text or "").split(":") if p.isdigit()]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def search(query, channels=CHANNELS, timeout=15):
    """返回 [{id, title, channel, seconds}]，只含白名单频道、30–240 秒的视频。"""
    url = ("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query) + "&sp=" + SHORT_FILTER)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        return []
    found = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v and "videoId" in v:
                try:
                    found.append({"id": v["videoId"], "title": v["title"]["runs"][0]["text"],
                                  "channel": v["ownerText"]["runs"][0]["text"],
                                  "seconds": _seconds(v.get("lengthText", {}).get("simpleText"))})
                except (KeyError, IndexError):
                    pass
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.loads(m.group(1)))
    low = [c.lower() for c in channels]
    return [v for v in found if 30 <= v["seconds"] <= 240 and any(c in v["channel"].lower() for c in low)]


def refresh_pool(channels=CHANNELS, topics=TOPICS, per_run=12, log=print):
    """每天最多刷新一次：随机挑几个话题搜，合并进已有的视频池。"""
    pool = load_pool()
    if time.time() - pool.get("updated", 0) < 20 * 3600 and pool["videos"]:
        return pool
    known = {v["id"]: v for v in pool["videos"]}
    for topic in random.sample(topics, min(per_run, len(topics))):
        topic = f"{random.choice(SEARCH_CHANNELS)} {topic}"
        try:
            for v in search(topic, channels):
                v["topic"] = topic
                known.setdefault(v["id"], v)
        except Exception as e:  # 没网、YouTube 改版：用旧的池子
            log(f"搜视频「{topic}」失败：{e}")
        time.sleep(1)
    pool = {"updated": time.time(), "videos": list(known.values())}
    POOL.parent.mkdir(parents=True, exist_ok=True)
    tmp = POOL.with_suffix(".tmp")
    tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(POOL)
    log(f"科普视频池：{len(pool['videos'])} 个")
    return pool


def load_pool():
    try:
        pool = json.loads(POOL.read_text(encoding="utf-8"))
        if isinstance(pool.get("videos"), list):
            return pool
    except (OSError, ValueError):
        pass
    return {"updated": 0, "videos": []}


def pick(seen, max_seconds, rng=random):
    """挑一个没看过、时长放得下的视频；池子空了用家长批准的固定列表。"""
    videos = load_pool()["videos"]
    try:
        videos = videos + [{"id": v["id"], "title": v["title"], "channel": v.get("channel", ""),
                            "seconds": v["seconds"]} for v in json.loads(CURATED.read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        pass
    fresh = [v for v in videos if v["id"] not in seen and v["seconds"] <= max_seconds]
    return rng.choice(fresh) if fresh else None
