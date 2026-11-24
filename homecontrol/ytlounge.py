"""让电视上的 YouTube 应用真正开始播某个视频（YouTube「投屏」用的 Lounge 协议）。

只用 DIAL 打开 YouTube 并带 v=ID，新版 YouTube TV 只会停在「等待手机连接」的投屏画面，
不会自己播。这里像手机 YouTube 一样：从 DIAL 拿 screenId → 换 loungeToken →
建立会话 → 发 setPlaylist，电视就直接播这个视频。不需要配对码，也不用登录。
"""
import json
import re
import urllib.parse
import urllib.request
import uuid

API = "https://www.youtube.com/api/lounge"


def screen_id(tv_ip, timeout=6):
    with urllib.request.urlopen(f"http://{tv_ip}:8080/ws/apps/YouTube", timeout=timeout) as r:
        m = re.search(r"<screenId>([^<]+)</screenId>", r.read().decode("utf-8", "replace"))
    return m.group(1) if m else None


def _post(url, data, timeout=15):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def play(tv_ip, video_id, name="Quiz"):
    sid = screen_id(tv_ip)
    if not sid:
        raise OSError("电视 YouTube 没有 screenId")
    tokens = json.loads(_post(f"{API}/pairing/get_lounge_token_batch", {"screen_ids": sid}))
    token = tokens["screens"][0]["loungeToken"]
    base = {"device": "REMOTE_CONTROL", "app": "youtube-desktop", "name": name, "id": str(uuid.uuid4()),
            "VER": "8", "CVER": "1", "loungeIdToken": token}
    first = _post(f"{API}/bc/bind?" + urllib.parse.urlencode({**base, "RID": "1"}), {"count": "0"})
    s = re.search(r'"c","([^"]+)"', first)
    g = re.search(r'"S","([^"]+)"', first)
    if not s or not g:
        raise OSError("YouTube 投屏会话没建立起来")
    q = {**base, "RID": "2", "SID": s.group(1), "gsessionid": g.group(1)}
    _post(f"{API}/bc/bind?" + urllib.parse.urlencode(q),
          {"count": "1", "ofs": "0", "req0__sc": "setPlaylist", "req0_videoId": video_id,
           "req0_currentTime": "0", "req0_currentIndex": "0", "req0_videoIds": video_id, "req0_listId": ""})
