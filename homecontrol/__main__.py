"""命令行入口:
  python -m homecontrol devices          列出家里所有设备
  python -m homecontrol status           查看受管控设备的在线/断网状态
  python -m homecontrol block switch     给设备断网
  python -m homecontrol unblock switch   恢复设备上网
  python -m homecontrol video            按配置重新生成提醒视频
  python -m homecontrol remind           马上在电视上播放提醒（测试用）
  python -m homecontrol run              开始监控：计时，超时接管电视
  python -m homecontrol quiz             只启动答题页（不碰电视），在电脑浏览器里预览
  python -m homecontrol live [秒数]       把摄像头和麦克风实时推到电视上，Ctrl+C 结束
  python -m homecontrol tv pair           和电视配对（电视上点「允许」）
  python -m homecontrol tv key home       按遥控器键：power home back ok up down left right
                                          vol+ vol- mute source hdmi play pause …，可连按多个
  python -m homecontrol tv apps           列出电视上的应用
  python -m homecontrol tv open <appId>   打开应用
  python -m homecontrol tv browser <URL>  用电视浏览器打开网页
"""
import sys

from .config import load_config, normalize_mac
from .fritz import Router


def connect(cfg):
    fb = cfg["fritzbox"]
    return Router(fb["address"], fb["user"], fb["password"])


def resolve(cfg, router, target):
    """target 可以是 [devices] 里的名字，也可以直接是 MAC。"""
    mac = cfg.get("devices", target, fallback=target)
    dev = router.find(mac)
    if dev is None:
        raise SystemExit(f"路由器里找不到设备: {target} ({normalize_mac(mac)})")
    return dev


def cmd_devices(cfg, router):
    devices = sorted(router.list_devices(), key=lambda d: (not d["online"], d["name"].lower()))
    print(f"{router.model}  共 {len(devices)} 台设备\n")
    print(f"{'状态':<4} {'名字':<32} {'IP':<16} {'MAC':<18} 连接")
    for d in devices:
        state = "在线" if d["online"] else "离线"
        print(f"{state:<4} {d['name'][:32]:<32} {d['ip'] or '-':<16} {d['mac']:<18} {d['interface'] or '-'}")


def cmd_status(cfg, router):
    if not cfg.has_section("devices") or not cfg.options("devices"):
        raise SystemExit("config.local.ini 的 [devices] 里还没有设备")
    for name in cfg.options("devices"):
        d = resolve(cfg, router, name)
        blocked = router.is_blocked(d["ip"]) if d["ip"] else False
        print(f"{name:<10} {d['name']:<28} {'在线' if d['online'] else '离线'}  "
              f"{'已断网' if blocked else '可上网'}")


def cmd_block(cfg, router, target, blocked):
    d = resolve(cfg, router, target)
    if not d["ip"]:
        raise SystemExit(f"{target} 当前没有 IP 地址，无法设置")
    router.set_blocked(d["ip"], blocked)
    print(f"{target} ({d['name']}, {d['ip']}): {'已断网' if router.is_blocked(d['ip']) else '已恢复上网'}")


def cmd_live(cfg, router, seconds):
    import time
    from .live import PATH, Broadcaster, start_live_server
    from .media import local_ip
    from .tv import SamsungTV

    live = cfg["live"] if cfg.has_section("live") else {}
    dev = router.find(cfg.get("devices", "tv"))
    if not dev or not dev["online"]:
        raise SystemExit("电视不在线，请先打开电视")
    tv = SamsungTV(dev["ip"], cfg.get("devices", "tv"))
    caster = Broadcaster(live.get("camera", "Integrated Camera"),
                         live.get("microphone", "Mikrofonarray"),
                         live.get("label", "家长 · 实时"))
    port = 8766
    start_live_server(caster, port)
    time.sleep(2)  # let ffmpeg open the camera before the TV connects
    if not caster.alive():
        raise SystemExit("摄像头或麦克风打不开（是否被别的程序占用？）")
    tv.play(f"http://{local_ip(tv.ip)}:{port}{PATH}", title="家长", mime="video/mp2t")
    print(f"正在直播到电视 {tv.ip}，按 Ctrl+C 结束", flush=True)
    try:
        end = time.time() + seconds if seconds else None
        while caster.alive() and (end is None or time.time() < end):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            tv.stop()
        except OSError:
            pass
        caster.stop()
        print("直播结束")


def tv_ip(cfg, router):
    """The TV can be listed twice (direct and via the repeater); use the IP that answers."""
    from .tv import SamsungTV
    suffix = normalize_mac(cfg.get("devices", "tv"))[-8:]
    for d in router.list_devices():
        if d["mac"][-8:] == suffix and d["online"] and d["ip"] and SamsungTV(d["ip"]).power_state():
            return d["ip"]
    raise SystemExit("电视不在线，请先打开电视")


def cmd_tv(cfg, router, args):
    import time
    from .remote import TVRemote
    ip = tv_ip(cfg, router)
    sub = args[0] if args else "pair"
    print(f"连接电视 {ip} …（第一次连接请在电视上点「允许」）", flush=True)
    with TVRemote(ip) as tv:
        if sub == "pair":
            print("已配对")
        elif sub == "key":
            for k in args[1:]:
                tv.key(k)
                time.sleep(0.4)
            print("已发送:", " ".join(args[1:]))
        elif sub == "apps":
            for app_id, name in tv.apps():
                print(f"{app_id:<28} {name}")
        elif sub == "open":
            tv.launch(args[1])
            print("已打开", args[1])
        elif sub == "browser":
            tv.open_browser(args[1])
            print("已在电视浏览器打开", args[1])
        else:
            raise SystemExit(__doc__)


def main(argv):
    # Windows 控制台默认 cp1252，打印中文会报错
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if not argv or argv[0] not in ("devices", "status", "block", "unblock", "video", "remind", "run", "live", "tv",
                                   "quiz"):
        print(__doc__)
        return 1
    cfg = load_config()
    cmd = argv[0]
    if cmd == "video":
        from .daemon import reminder_options
        from .reminder import build_video
        print(build_video(**reminder_options(cfg)))
        return 0
    router = connect(cfg)
    if cmd == "tv":
        cmd_tv(cfg, router, argv[1:])
        return 0
    if cmd == "live":
        cmd_live(cfg, router, int(argv[1]) if len(argv) > 1 else 0)
        return 0
    if cmd == "quiz":
        import time
        from .daemon import Watcher
        from .quizserver import start_quiz_server
        watcher = Watcher(cfg, router)
        start_quiz_server(watcher, watcher.quiz_port)
        print(f"答题页: http://localhost:{watcher.quiz_port}/quiz （答对的时间会记入今天的账本），Ctrl+C 结束", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
    if cmd in ("remind", "run"):
        from .daemon import Watcher
        watcher = Watcher(cfg, router)
        if cmd == "remind":
            if not watcher.remind():
                return 1
            import time
            time.sleep(45)  # keep serving the video while the TV streams it
            return 0
        watcher.run()
    elif cmd == "devices":
        cmd_devices(cfg, router)
    elif cmd == "status":
        cmd_status(cfg, router)
    else:
        if len(argv) < 2:
            raise SystemExit(f"用法: python -m homecontrol {cmd} <设备名或MAC>")
        cmd_block(cfg, router, argv[1], cmd == "block")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
