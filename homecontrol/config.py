import configparser
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.local.ini"


def load_config():
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_PATH, encoding="utf-8"):
        raise SystemExit(f"找不到配置文件: {CONFIG_PATH}")
    if not cfg.get("fritzbox", "password", fallback=""):
        raise SystemExit(f"请先在 {CONFIG_PATH} 里填写 FRITZ!Box 密码")
    return cfg


def normalize_mac(mac):
    return mac.strip().upper().replace("-", ":")
