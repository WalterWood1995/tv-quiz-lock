"""The watcher: counts today's screen time and, once over the limit, takes over the TV —
with the quiz screensaver if [quiz] is enabled, otherwise with the reminder video."""
import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

from .fritzweb import FritzWeb
from .media import local_ip, start_server
from .quiz import QuizEngine, Rules
from .quizserver import start_quiz_server
from .reminder import VIDEO, build_video
from .tv import SamsungTV

STATE = Path(__file__).resolve().parent.parent / "data" / "usage.json"
PORT = 8765
QUIZ_PORT = 8767
HEARTBEAT_TIMEOUT = 12  # 答题页超过这么多秒没心跳，就认为被切走了
RELAUNCH_GAP = 15       # 两次重新打开答题页之间至少间隔


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
        self.engine = QuizEngine(self.rules, cfg.get("quiz", "bank_dir", fallback="").strip() or None)
        self.quiz_lang = self.rules.language
        self.locked = False  # 路由器上电视当前是否被断网（跨天时用来解除昨天的锁）
        self.last_heartbeat = 0
        self.last_launch = 0

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
                if tv and tv.power_state() == "on":
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
            self.router.set_blocked(tv.ip, blocked)
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
            if not self.locked:
                continue
            now = time.time()
            if now - self.last_heartbeat < HEARTBEAT_TIMEOUT or now - self.last_launch < RELAUNCH_GAP:
                continue
            try:
                tv = self.tv or self._tv()
                if tv is None or tv.power_state() != "on":
                    continue
                browser = browser or next((b for b in BROWSER_IDS if tv.app_visible(b) is not None), None)
                if browser is None:
                    log("电视上找不到答题应用或浏览器")
                    continue
                self.last_launch = now
                tv.launch_app(browser)
                log(f"答题页不在前台，已在电视上启动 {browser}")
            except Exception as e:
                log(f"调出答题页失败: {e}")

    def _public_state(self, state):
        return {"locked": state["tv_locked"], "remaining": self.remaining(state), "used": self.used(state),
                "limit": self.limit, "earnedToday": state["quiz"]["earned"],
                "rewards": self.rules.reward, "penaltyEasy": self.rules.penalty_easy,
                "cooldown": self.engine.cooling_down(), "lang": self.quiz_lang}

    def quiz_state(self):
        with self.lock:
            return self._public_state(load_state())

    def quiz_question(self, difficulty):
        with self.lock:
            return self.engine.next_question(difficulty).public(self.quiz_lang)

    def quiz_answer(self, qid, choice):
        action = None
        with self.lock:
            state = load_state()
            result = self.engine.answer(qid, choice, state["quiz"]["earned"], self.remaining(state))
            if "error" in result:
                return result
            delta = result["delta"]
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
        self.last_heartbeat = time.time()

    def start_quiz(self):
        start_quiz_server(self, self.quiz_port)
        threading.Thread(target=self.enforce_loop, daemon=True).start()
        log(f"答题屏保已启用，答题页端口 {self.quiz_port}，题库 {len(self.engine.static)} 道选择题 + 数学自动出题")

    def run(self):
        log(f"开始监控 {', '.join(self.track)}，每天上限 {self.limit} 分钟，"
            + ("超时后电视进入答题屏保" if self.quiz_on else f"超时后每 {self.repeat} 分钟提醒一次"))
        if self.quiz_on:
            # 重启后接着锁：今天的账本里电视已经是锁定状态，就继续守着答题页
            self.locked = load_state()["tv_locked"]
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
