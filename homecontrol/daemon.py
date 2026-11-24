"""The watcher: counts today's screen time and, once over the limit, takes over the TV —
with the quiz screensaver if [quiz] is enabled, otherwise with the reminder video."""
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from .fritzweb import FritzWeb
from .media import local_ip, start_server
from .config import write_config
from .quiz import LANGUAGES, PENALTY_FACTOR, QuizEngine, Rules
from .quizserver import start_quiz_server
from .reminder import VIDEO, build_video
from . import ytpick
from .tv import SamsungTV

STATE = Path(__file__).resolve().parent.parent / "data" / "usage.json"
PORT = 8765
QUIZ_PORT = 8767
HEARTBEAT_TIMEOUT = 12  # 答题页超过这么多秒没心跳，就认为被切走了
RELAUNCH_GAP = 15       # 两次重新打开答题页之间至少间隔
VIDEOS = Path(__file__).resolve().parent.parent / "assets" / "videos.json"  # 家长批准的视频，只放这些
VIDEO_MARGIN = 30       # 缓冲、广告的余量（秒），到点就断网
VIDEO_GRACE = 15        # 开播后这么久内不查电视前台（应用可能还在切换）
VIDEO_AWAY_CHECKS = 3   # 每 5 秒查一次，连续这么多次不在前台才算离开


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_state():
    today = date.today().isoformat()
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("date") == today:
            state.setdefault("quiz", {"earned": 0, "penalty": 0})
            state.setdefault("tv_locked", False)
            return state
    except (OSError, ValueError):
        pass
    return {"date": today, "minutes": {}, "last_reminder": 0,
            "quiz": {"earned": 0, "penalty": 0}, "tv_locked": False}


def save_state(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


class Watcher:
    def __init__(self, cfg, router):
        self.cfg, self.router = cfg, router
        fb = cfg["fritzbox"]
        self.web = FritzWeb(fb["address"], fb["user"], fb["password"])
        lim = cfg["limits"]
        self.limit = lim.getint("daily_minutes", 60)
        self.repeat = lim.getint("repeat_minutes", 5)
        self.track = [t.strip() for t in lim.get("track", "switch, ipad, tv").split(",") if t.strip()]
        self.tv_name = "tv" if cfg.has_option("devices", "tv") else None
        self.tv = None
        self.url = None
        # 答题屏保（[quiz] enabled = true 时启用）
        self.lock = threading.RLock()  # 保护 usage.json：主循环和答题页请求会同时读写
        self.quiz_on = bool(self.tv_name) and cfg.getboolean("quiz", "enabled", fallback=False)
        self.quiz_port = cfg.getint("quiz", "port", fallback=QUIZ_PORT)
        self.rules = Rules.from_config(cfg)
        self.engine = QuizEngine(self.rules, cfg.get("quiz", "bank_dir", fallback="").strip() or None,
                                 stats_path=STATE.with_name("question_stats.json"))
        self.quiz_lang = self.rules.language
        self.locked = False  # 路由器上电视当前是否被断网（跨天时用来解除昨天的锁）
        self.last_heartbeat = 0
        self.quiz_seen = 0  # 只认答题页自己的心跳，enforce_loop 改 last_heartbeat 不影响它
        self.cast_logged = False
        self.pause_until = 0  # 家长面板暂停刷题：到这个时间前不调答题页、不计电视时间
        self.parent_ips = {ip.strip() for ip in cfg.get("quiz", "parent_ips", fallback=(
            "127.0.0.1, 192.168.178.113, 192.168.178.44, 192.168.178.46")).split(",") if ip.strip()}
        self.last_launch = 0
        self.video_limit = cfg.getint("quiz", "daily_video_minutes", fallback=30)
        self.gesture_on = cfg.getboolean("quiz", "gesture", fallback=False)  # 实测开摄像头后平板掉线，默认关
        self.video_until = 0  # 正在放视频时的截止时间；放视频不看答题页心跳，由电视前台和这个时间兜底
        self.video_started = 0
        self.video_stay = False
        self.video_id = None
        self.video_away = 0
        # 必看科普视频：答几道题后随机插一个，在电视自带的 YouTube 应用里放，不能跳过
        self.video_mode = None  # "youtube" 表示正在放必看视频
        self.forced_info = None
        self.video_relaunches = 0
        self.forced_on = cfg.getboolean("quiz", "forced_videos", fallback=True)
        self.forced_per_day = cfg.getint("quiz", "forced_videos_per_day", fallback=3)
        self.forced_every = (cfg.getint("quiz", "forced_video_min_questions", fallback=4),
                             cfg.getint("quiz", "forced_video_max_questions", fallback=8))
        self.video_channels = [c.strip() for c in cfg.get("quiz", "video_channels", fallback="").split(",")
                               if c.strip()] or ytpick.CHANNELS
        self.since_video = 0
        self.next_video_after = random.randint(*self.forced_every)

    def _tv(self):
        """Find the TV's current IP via the router (it may change while the TV sleeps)."""
        if not self.tv_name:
            return None
        dev = self.router.find(self.cfg.get("devices", self.tv_name))
        if dev and dev["ip"] and (self.tv is None or self.tv.ip != dev["ip"]):
            self.tv = SamsungTV(dev["ip"], self.cfg.get("devices", self.tv_name))
        return self.tv

    def remind(self):
        """Put the reminder on the TV now; wakes the TV first if it is off."""
        tv = self._tv()
        if tv is None:
            log("没有配置电视，无法显示提醒")
            return False
        if not VIDEO.exists():
            build_video(**reminder_options(self.cfg))
        if self.url is None:
            start_server(PORT)
            self.url = f"http://{local_ip(tv.ip)}:{PORT}/{VIDEO.name}"
        if tv.power_state() != "on":
            log("电视没开，尝试唤醒")
            tv.wake()
            for _ in range(20):
                time.sleep(2)
                if tv.power_state() == "on":
                    break
            else:
                log("电视唤醒失败（需要在电视上开启「通过手机开机」）")
                return False
            time.sleep(5)  # let the media player come up
        tv.play(self.url)
        log(f"已在电视上播放提醒 ({tv.ip})")
        return True

    # ---- 时间账本 ----

    def used(self, state):
        return sum(state["minutes"].get(n, 0) for n in self.track)

    def remaining(self, state):
        q = state["quiz"]
        return self.limit + q["earned"] - q["penalty"] - self.used(state)

    def measure(self):
        """问路由器、FRITZ!Box 和电视这一分钟的使用情况。这些请求很慢（偶尔超时 10 秒以上），
        所以一定不能拿着账本锁做——否则电视答题页的请求全被卡住（实测：电视显示连不上电脑）。
        返回 {设备: ("set", 分钟) 或 ("inc", 1)}。"""
        try:
            fritz = self.web.online_minutes_today()
        except Exception as e:  # keep counting the TV even if the web UI hiccups
            log(f"读取 FRITZ!Box 上网时长失败: {e}")
            fritz = {}
        out = {}
        for name in self.track:
            if name == self.tv_name:
                tv = self._tv()
                # 答题页（含看视频、闯关）开着的时间不算看电视；没锁时孩子也会来答题赚时间
                quizzing = time.time() - self.quiz_seen < HEARTBEAT_TIMEOUT
                # 家长投屏的时间也不算
                if self.paused() or self.video_mode == "youtube":
                    continue
                if tv and not quizzing and tv.power_state() == "on" and not (tv.casting() and not self.video_until):
                    out[name] = ("inc", 1)
                continue
            mac = self.cfg.get("devices", name, fallback=name)
            dev = self.router.find(mac)
            # 经过中继器的设备在路由器里有两个名字（如 Kid-Switch / PC-192-168-178-86），
            # FRITZ!OS 的上网时长只记在其中一个名字下：哪个名字有记录就用哪个
            names = {d["name"] for d in self.router.list_devices() if d["mac"][-8:] == mac.upper()[-8:]}
            measured = [fritz[n] for n in names if n in fritz]
            if measured:
                out[name] = ("set", max(measured))  # FRITZ!OS counts real traffic
            elif dev and dev["online"]:
                out[name] = ("inc", 1)  # fallback: connected = in use
        return out

    def tick(self, state, measured=None):
        """把这一分钟的使用记进账本；返回要对路由器做的动作（"block" / "unblock" / None）。"""
        if measured is None:
            measured = self.measure()
        mins = state["minutes"]
        for name, (kind, value) in measured.items():
            if name == self.tv_name and state["tv_locked"]:
                continue  # 电视在答题屏保上的时间不算看电视
            mins[name] = value if kind == "set" else mins.get(name, 0) + value
        total = self.used(state)
        detail = ", ".join(f"{n} {mins.get(n, 0)}" for n in self.track)
        q = state["quiz"]
        bonus = f"，答题 +{q['earned']} -{q['penalty']}" if q["earned"] or q["penalty"] else ""
        log(f"今日 {total}/{self.limit} 分钟 ({detail}){bonus}，余额 {self.remaining(state)}")
        if self.quiz_on:
            return self.lock_transition(state)
        if total >= self.limit and time.time() - state["last_reminder"] >= self.repeat * 60:
            try:
                if self.remind():
                    state["last_reminder"] = time.time()
            except Exception as e:
                log(f"显示提醒失败: {e}")
        return None

    # ---- 答题屏保 ----

    def _set_tv_internet(self, blocked):
        tv = self._tv()
        if tv is None:
            return False
        try:
            # 电视在路由器里可能同时有直连和经放大器两条记录，两个 IP 都要改
            self.router.set_blocked_all(self.cfg.get("devices", self.tv_name), blocked)
            return True
        except Exception as e:
            log(f"{'断网' if blocked else '恢复上网'}失败: {e}")
            return False

    def lock_transition(self, state):
        """余额用完就锁电视，余额回正或跨天就解锁。只改账本，返回要对路由器做的动作，
        路由器请求由调用方在放开账本锁之后执行（apply_router）。"""
        should_lock = self.remaining(state) <= 0
        if should_lock and not state["tv_locked"]:
            state["tv_locked"] = True
            self.locked = True
            self.video_until = 0  # 看视频时余额用完：断网，回答题页
            self.video_mode = None
            self.last_heartbeat = 0  # 立刻打开答题页
            log("时间用完：电视断外网，开始显示答题屏保")
            return "block"
        if not should_lock and (state["tv_locked"] or self.locked):
            state["tv_locked"] = False
            self.locked = False
            log(f"已解锁：电视恢复上网，余额 {self.remaining(state)} 分钟")
            return "unblock"
        return None

    def apply_router(self, action):
        if action:
            self._set_tv_internet(action == "block")

    def update_lock(self, state):
        self.apply_router(self.lock_transition(state))

    def quiz_url(self, tv):
        return f"http://{local_ip(tv.ip)}:{self.quiz_port}/quiz"

    def enforce_loop(self):
        """锁定期间，答题页不在前台就把电视上的答题应用（tvapp/）调到前台。

        没配 [quiz] tv_app 时退回到调出电视浏览器（答题页需设为浏览器主页）——
        但 QN85B 不会把浏览器切到前台，所以这台电视必须用答题应用。"""
        from .remote import BROWSER_IDS
        browser = self.cfg.get("quiz", "tv_app", fallback="").strip() or None
        while True:
            time.sleep(5)
            if self.video_until:
                self.check_video()
                continue
            if not self.locked or self.paused():
                continue
            now = time.time()
            if now - self.last_heartbeat < HEARTBEAT_TIMEOUT or now - self.last_launch < RELAUNCH_GAP:
                continue
            try:
                tv = self.tv or self._tv()
                if tv is None or tv.power_state() != "on":
                    continue
                if tv.casting():  # 正在投屏：锁着也不打断，投完再调回答题页
                    if not self.cast_logged:
                        log("电视正在投屏，暂不调出答题页")
                    self.cast_logged = True
                    continue
                self.cast_logged = False
                browser = browser or next((b for b in BROWSER_IDS if tv.app_visible(b) is not None), None)
                if browser is None:
                    log("电视上找不到答题应用或浏览器")
                    continue
                self.last_launch = now
                tv.launch_app(browser)
                log(f"答题页不在前台，已在电视上启动 {browser}")
            except Exception as e:
                log(f"调出答题页失败: {e}")

    # ---- 科普视频 ----

    def _videos_left(self, state):
        return self.video_limit * 60 - state.get("video_seconds", 0)

    def quiz_videos(self):
        with self.lock:
            state = load_state()
            left, seen = self._videos_left(state), state.get("videos_seen", [])
        videos = json.loads(VIDEOS.read_text(encoding="utf-8"))
        return {"secondsLeft": left, "seen": seen, "videos": [v for v in videos if v["seconds"] <= left]}

    def video_start(self, video_id):
        video = next((v for v in json.loads(VIDEOS.read_text(encoding="utf-8")) if v["id"] == video_id), None)
        if video is None:
            return {"error": "unknown video"}
        with self.lock:
            # 路由器开网要几秒，孩子多按几次确定，不能重复扣额度
            if self.video_until and self.video_id == video_id:
                return {"ok": True}
            state = load_state()
            if video["seconds"] > self._videos_left(state):
                return {"error": "no video time left"}
            state["video_seconds"] = state.get("video_seconds", 0) + video["seconds"]
            state["videos_seen"] = state.get("videos_seen", []) + [video_id]
            save_state(state)
            locked = state["tv_locked"]
            now = time.time()
            self.video_id = video_id
            self.video_away = 0
            self.video_started = now
            self.video_until = now + video["seconds"] + VIDEO_MARGIN
        if locked and not self._set_tv_internet(False):
            self.video_until = 0
            return {"error": "router"}
        log(f"开始放视频 {video_id}，{video['seconds']} 秒")
        return {"ok": True}

    def video_thumb(self, video_id):
        """视频缩略图：电视断网时也要能显示，所以由平板下载并缓存。只取列表里的视频。"""
        if not any(v["id"] == video_id for v in json.loads(VIDEOS.read_text(encoding="utf-8"))):
            return None
        path = STATE.parent / "thumbs" / f"{video_id}.jpg"
        if not path.exists():
            with urllib.request.urlopen(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", timeout=10) as r:
                data = r.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return path.read_bytes()

    def video_stop(self, stay=False):
        """stay：视频放完要在页面上闯关，只断网，不重开答题页。"""
        self.video_stay = stay
        if self.video_until:
            self.video_until = 1  # 交给 enforce_loop 收尾

    # ---- 必看科普视频（电视 YouTube 应用） ----

    def forced_video_due(self, state):
        return (self.forced_on and not self.paused() and not self.video_until
                and self.since_video >= self.next_video_after
                and state.get("forced_videos", 0) < self.forced_per_day)

    def forced_video_start(self, state):
        """挑一个没看过的科普短视频，开网后在电视 YouTube 里放。返回给答题页的说明，挑不到返回 None。
        调用方拿着账本锁；路由器和电视的请求放到后台线程里做。"""
        video = ytpick.pick(set(state.get("videos_seen", [])), max(60, self._videos_left(state)))
        if video is None:
            return None
        tv = self.tv or self._tv()
        if tv is None:
            return None
        state["forced_videos"] = state.get("forced_videos", 0) + 1
        state["videos_seen"] = state.get("videos_seen", []) + [video["id"]]
        state["video_seconds"] = state.get("video_seconds", 0) + video["seconds"]
        save_state(state)
        self.since_video = 0
        self.next_video_after = random.randint(*self.forced_every)
        now = time.time()
        self.video_mode = "youtube"
        self.video_id = video["id"]
        self.video_away = 0
        self.video_relaunches = 0
        self.video_stay = False
        self.video_started = now + 20  # 开网、打开 YouTube、投屏开播要十几秒
        self.video_until = now + 20 + video["seconds"] + VIDEO_MARGIN
        locked = state["tv_locked"]

        def give_back():
            """没放成：不算今天的次数，这个视频以后还能出。"""
            with self.lock:
                s = load_state()
                s["forced_videos"] = max(0, s.get("forced_videos", 0) - 1)
                s["video_seconds"] = max(0, s.get("video_seconds", 0) - video["seconds"])
                s["videos_seen"] = [x for x in s.get("videos_seen", []) if x != video["id"]]
                save_state(s)
            self.video_until = 1

        def go():
            if locked and not self._set_tv_internet(False):
                log("必看视频：开网失败，取消")
                give_back()
                return
            time.sleep(3 if locked else 0)
            try:
                how = self._play_youtube(video["id"])
                log(f"必看视频 {video['id']}（{video['channel']}，{video['seconds']} 秒）已在电视 YouTube 打开（{how}）："
                    f"{video['title']}")
            except Exception as e:
                log(f"必看视频打不开：{e}")
                give_back()
        threading.Thread(target=go, daemon=True).start()
        self.forced_info = {"title": video["title"], "channel": video["channel"], "seconds": video["seconds"]}
        return {"video": self.forced_info}

    def _play_youtube(self, video_id):
        """电视在路由器里可能有两个 IP（直连 / 经放大器），缓存的那个可能已经不通：挨个试。"""
        tv = self._tv()
        tried = []
        candidates = [tv] if tv else []
        try:
            for d in self.router.find_all(self.cfg.get("devices", self.tv_name)):
                if d["ip"] and all(c.ip != d["ip"] for c in candidates):
                    candidates.append(SamsungTV(d["ip"], self.cfg.get("devices", self.tv_name)))
        except Exception as e:  # 路由器一时问不到：先用已知的 IP 试
            log(f"问路由器电视 IP 失败：{e}")
        for c in candidates:
            try:
                how = c.play_youtube(video_id)
                self.tv = c
                return f"{how} @ {c.ip}"
            except Exception as e:
                tried.append(f"{c.ip}: {e}")
        raise OSError("；".join(tried) or "找不到电视")

    def _check_forced(self, tv, now):
        """必看视频：没看完就离开 YouTube，重新打开同一个视频；看完了才算结束。"""
        if now >= self.video_until:
            return True
        if now > self.video_started + VIDEO_GRACE and tv and tv.power_state() == "on":
            self.video_away = self.video_away + 1 if tv.youtube_visible() is False else 0
            if self.video_away >= VIDEO_AWAY_CHECKS:
                self.video_away = 0
                if self.video_relaunches >= 5:
                    log("必看视频：多次离开 YouTube，结束")
                    return True
                self.video_relaunches += 1
                left = int(self.video_until - now)
                log(f"必看视频还没看完（还剩约 {left} 秒），重新打开")
                try:
                    self._play_youtube(self.video_id)
                    self.video_started = now + 15
                except Exception as e:
                    log(f"重新打开必看视频失败：{e}")
        return False

    def check_video(self):
        """到时间或电视离开答题应用就结束：先断网，再把答题页重新打开。"""
        now = time.time()
        if self.video_mode == "youtube":
            tv = self._tv()  # 每次都问路由器：电视的 IP 可能刚换过
            if not self._check_forced(tv, now):
                return
            if self.locked and not self._set_tv_internet(True):
                return
            self.video_until = 0
            self.video_mode = None
            self.last_heartbeat = 0
            log("必看视频看完了，回答题页")
            app = self.cfg.get("quiz", "tv_app", fallback="").strip()
            try:
                if tv and tv.power_state() == "on":
                    tv.close_youtube()
                    time.sleep(2)
                    if app:
                        tv.launch_app(app)
                        self.last_launch = time.time()
            except Exception as e:
                log(f"必看视频结束后回答题页失败: {e}")
            return
        app = self.cfg.get("quiz", "tv_app", fallback="").strip()
        tv = self.tv or self._tv()
        expired = now >= self.video_until
        if not expired and now - self.video_started > VIDEO_GRACE and tv and app:
            # 实测电视偶尔短暂报告不在前台，连续几次才算离开
            self.video_away = self.video_away + 1 if tv.app_visible(app) is False else 0
            if self.video_away >= VIDEO_AWAY_CHECKS:
                log("电视离开了答题应用，结束视频")
                expired = True
        if not expired:
            return
        if self.locked and not self._set_tv_internet(True):
            return  # 断网没成功，5 秒后再试，不放过
        self.video_until = 0
        self.last_heartbeat = 0
        log("视频结束")
        if self.video_stay:
            self.video_stay = False
            self.last_heartbeat = time.time()  # 页面还在，不用重开
            return
        try:
            if tv and app and tv.power_state() == "on":
                if tv.app_visible(app):
                    tv.close_app(app)  # 视频页的脚本可能已停，重开才回到答题页
                    time.sleep(2)
                    tv.launch_app(app)
                    self.last_launch = time.time()
        except Exception as e:
            log(f"视频结束后重开答题页失败: {e}")

    def _public_state(self, state):
        return {"locked": state["tv_locked"], "remaining": self.remaining(state), "used": self.used(state),
                "limit": self.limit, "earnedToday": state["quiz"]["earned"],
                "rewards": self.rules.reward, "penaltyEasy": self.rules.reward["easy"] * PENALTY_FACTOR["easy"],
                "penalties": {d: self.rules.reward[d] * f for d, f in PENALTY_FACTOR.items()},
                "cooldown": self.engine.cooling_down(), "lang": self.quiz_lang,
                "forcedVideo": self.video_mode == "youtube"}

    def quiz_state(self):
        with self.lock:
            return self._public_state(load_state())

    # ---- 家长面板 ----

    def paused(self):
        return time.time() < self.pause_until

    def parent_state(self):
        state = self.quiz_state()
        state["pausedFor"] = max(0, int((self.pause_until - time.time()) / 60 + 0.99))
        return state

    def parent_pause(self, minutes):
        """minutes > 0：暂停刷题这么多分钟（投屏、家长自己用电视）；0：马上恢复。"""
        minutes = max(0, min(int(minutes), 24 * 60))
        self.pause_until = time.time() + minutes * 60 if minutes else 0
        log(f"家长面板：暂停刷题 {minutes} 分钟" if minutes else "家长面板：恢复刷题")
        if not minutes:
            self.last_heartbeat = 0  # 锁着就马上调回答题页
        return self.parent_state()

    # ---- 答题记录和统计 ----

    def _record(self, q, correct, delta, skipped=False):
        """每次作答记一行到 data/answers.jsonl，家长面板的统计从这里算。"""
        if q is None:
            return
        row = {"t": int(time.time()), "day": date.today().isoformat(), "id": q.id, "subject": q.subject,
               "difficulty": q.difficulty, "correct": bool(correct), "skipped": skipped, "delta": delta}
        try:
            with open(STATE.with_name("answers.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            log(f"答题记录写不进去：{e}")

    def parent_stats(self, days=7):
        rows = []
        answers = STATE.with_name("answers.jsonl")
        if answers.exists():
            for line in answers.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        first = (date.today() - timedelta(days=days - 1)).isoformat()
        rows = [r for r in rows if r["day"] >= first]
        labels = LANGUAGES[self.quiz_lang]["subjects"]

        def summary(rs):
            return {"answered": len(rs), "correct": sum(r["correct"] for r in rs),
                    "skipped": sum(r["skipped"] for r in rs),
                    "earned": round(sum(r["delta"] for r in rs if r["delta"] > 0), 1),
                    "lost": round(-sum(r["delta"] for r in rs if r["delta"] < 0), 1)}
        by_day = {}
        by_subject = {}
        for r in rows:
            by_day.setdefault(r["day"], []).append(r)
            by_subject.setdefault(r["subject"], []).append(r)
        recent = []
        for r in rows[-30:][::-1]:
            recent.append({"time": time.strftime("%m-%d %H:%M", time.localtime(r["t"])),
                           "subject": labels.get(r["subject"], r["subject"]), "difficulty": r["difficulty"],
                           "result": "skip" if r["skipped"] else "ok" if r["correct"] else "wrong",
                           "delta": r["delta"], "id": r["id"]})
        today = date.today().isoformat()
        return {"today": summary(by_day.get(today, [])),
                "days": [{"day": d, **summary(by_day[d])} for d in sorted(by_day, reverse=True)],
                "subjects": sorted(({"subject": labels.get(s, s), **summary(rs)} for s, rs in by_subject.items()),
                                   key=lambda x: -x["answered"]),
                "recent": recent, "retired": sum(1 for k in self.engine.stats if self.engine.retired(k))}

    # ---- 题库和规则设置 ----

    def parent_settings(self):
        known = LANGUAGES[self.quiz_lang]["subjects"]
        return {"grade": self.rules.grade, "harder": self.rules.harder, "dailyMinutes": self.limit,
                "dailyEarnCap": self.rules.daily_earn_cap, "subjects": self.rules.subjects,
                "allSubjects": [{"key": k, "label": v} for k, v in known.items()],
                "rewards": self.rules.reward, "rewardReading": self.rules.reward_reading,
                "bankSize": len(self.engine.static)}

    def parent_save_settings(self, body):
        """家长面板改设置：写回 config.local.ini（保留注释），马上换新题库，不用重启。"""
        known = LANGUAGES[self.quiz_lang]["subjects"]
        changes = {}
        if "grade" in body:
            changes[("quiz", "grade")] = str(max(1, min(13, int(body["grade"]))))
        if "harder" in body:
            changes[("quiz", "harder")] = "true" if body["harder"] else "false"
        if "subjects" in body:
            subs = [s for s in body["subjects"] if s in known]
            if not subs:
                return {"error": "至少要选一个科目"}
            changes[("quiz", "subjects")] = ", ".join(subs)
        if "dailyMinutes" in body:
            changes[("limits", "daily_minutes")] = str(max(0, min(600, int(body["dailyMinutes"]))))
        if "dailyEarnCap" in body:
            changes[("quiz", "daily_earn_cap")] = str(max(0, min(600, int(body["dailyEarnCap"]))))
        with self.lock:
            write_config(changes)
            for (sec, key), value in changes.items():
                self.cfg.set(sec, key, value)
            self.limit = self.cfg["limits"].getint("daily_minutes", 60)
            self.rules = Rules.from_config(self.cfg)
            stats = self.engine.stats
            self.engine = QuizEngine(self.rules, self.cfg.get("quiz", "bank_dir", fallback="").strip() or None,
                                     stats_path=STATE.with_name("question_stats.json"))
            self.engine.stats = stats
            state = load_state()
            action = self.lock_transition(state) if self.quiz_on else None
            save_state(state)
        self.apply_router(action)
        log("家长面板改设置：" + "，".join(f"{k}={v}" for (_, k), v in changes.items()))
        return self.parent_settings()

    def parent_grant(self, minutes):
        """家长直接加（或减）孩子今天的时间。"""
        minutes = max(-120, min(240, float(minutes)))
        with self.lock:
            state = load_state()
            state["quiz"]["earned"] += minutes
            action = self.lock_transition(state) if self.quiz_on else None
            save_state(state)
        self.apply_router(action)
        log(f"家长面板：{'加' if minutes >= 0 else '减'}时 {abs(minutes):g} 分钟")
        return self.parent_state()

    # ---- 更新程序 ----

    def parent_update(self):
        """git pull 拉最新代码和题库；有新代码就 2 秒后重启自己（Air Agent 上由 systemd 接着跑）。"""
        root = STATE.parent.parent

        def git(*args):
            r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            return r.returncode, (r.stdout + r.stderr).strip()
        _, before = git("rev-parse", "--short", "HEAD")
        code, out = git("pull", "--no-rebase", "--no-edit")
        _, after = git("rev-parse", "--short", "HEAD")
        _, logline = git("log", "-1", "--format=%cd %s", "--date=format:%Y-%m-%d %H:%M")
        log(f"家长面板：更新 {before} → {after}")
        changed = code == 0 and before != after
        if changed:
            threading.Timer(2, self._restart).start()
        return {"ok": code == 0, "before": before, "after": after, "changed": changed, "latest": logline,
                "output": out[-800:]}

    def parent_version(self):
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%h · %cd · %s", "--date=format:%Y-%m-%d %H:%M"],
                               cwd=STATE.parent.parent, capture_output=True, text=True, timeout=10,
                               encoding="utf-8", errors="replace")
            return {"version": r.stdout.strip()}
        except (OSError, subprocess.SubprocessError) as e:
            return {"version": f"读不到版本：{e}"}

    def _restart(self):
        log("更新后重启答题服务")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def quiz_question(self, difficulty):
        with self.lock:
            state = load_state()
            if self.video_mode == "youtube":  # 没看完就回到答题页：还是先看视频
                return {"video": self.forced_info}
            if self.forced_video_due(state):
                forced = self.forced_video_start(state)
                if forced:
                    return forced
            q = self.engine.next_question(difficulty)
            return q if isinstance(q, dict) else q.public(self.quiz_lang)

    def quiz_skip(self, qid):
        with self.lock:
            state = load_state()
            q = self.engine.pending.get(qid, (None,))[0]
            result = self.engine.skip(qid, self.remaining(state))
            if "error" not in result:
                self._record(q, False, result["delta"], skipped=True)
                if result["delta"] < 0:
                    state["quiz"]["penalty"] -= result["delta"]
                    save_state(state)
                result["state"] = self._public_state(state)
                log(f"答题 {qid} 跳过 {result['delta']} 分钟，看讲解 {result['wait']} 秒")
            return result

    def quiz_answer(self, qid, choice):
        action = None
        with self.lock:
            state = load_state()
            q = self.engine.pending.get(qid, (None,))[0]
            result = self.engine.answer(qid, choice, state["quiz"]["earned"], self.remaining(state))
            if "error" in result:
                return result
            delta = result["delta"]
            self._record(q, result["correct"], delta)
            self.since_video += 1
            log(f"答题 {qid} 选 {choice}，{'对' if result['correct'] else '错'}（正确 {result['answer']}）")
            if delta > 0:
                state["quiz"]["earned"] += delta
            elif delta < 0:
                state["quiz"]["penalty"] -= delta
            if delta:
                log(f"答题{'+' if delta > 0 else ''}{delta} 分钟，余额 {self.remaining(state)}")
            if self.quiz_on:
                action = self.lock_transition(state)
            save_state(state)
            result["state"] = self._public_state(state)
        self.apply_router(action)  # 恢复上网要访问路由器，放开账本锁之后再做
        return result

    def heartbeat(self):
        self.last_heartbeat = self.quiz_seen = time.time()

    def _video_pool_loop(self):
        while True:
            try:
                ytpick.refresh_pool(self.video_channels, log=log)
            except Exception as e:
                log(f"刷新科普视频池失败：{e}")
            time.sleep(3 * 3600)

    def start_quiz(self):
        start_quiz_server(self, self.quiz_port)
        if self.forced_on:
            threading.Thread(target=self._video_pool_loop, daemon=True).start()
        threading.Thread(target=self.enforce_loop, daemon=True).start()
        log(f"答题屏保已启用，答题页端口 {self.quiz_port}，题库 {len(self.engine.static)} 道选择题 + 数学自动出题")

    def run(self):
        log(f"开始监控 {', '.join(self.track)}，每天上限 {self.limit} 分钟，"
            + ("超时后电视进入答题屏保" if self.quiz_on else f"超时后每 {self.repeat} 分钟提醒一次"))
        if self.quiz_on:
            # 重启后接着锁：今天的账本里电视已经是锁定状态，就继续守着答题页
            self.locked = load_state()["tv_locked"]
            if self.locked:
                self.apply_router("block")  # 上次可能在放视频时退出，网还开着
            self.start_quiz()
        while True:
            self.cycle()
            time.sleep(60)

    def cycle(self):
        """一分钟一轮：慢的网络请求都不拿账本锁，答题页的请求随时能马上得到回应。"""
        try:
            measured = self.measure()
        except Exception as e:
            log(f"本轮读取使用情况出错: {e}")
            measured = {}
        action = None
        with self.lock:
            state = load_state()
            try:
                action = self.tick(state, measured)
            except Exception as e:
                log(f"本轮出错: {e}")
            save_state(state)
        self.apply_router(action)


def reminder_options(cfg):
    if not cfg.has_section("reminder"):
        return {}
    r = cfg["reminder"]
    opts = {k: r[k] for k in ("text", "voice", "avatar", "voice_file") if r.get(k)}
    root = Path(__file__).resolve().parent.parent
    for k in ("avatar", "voice_file"):
        if k in opts:
            opts[k] = str(root / opts[k])
    return opts
