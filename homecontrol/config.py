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


def write_config(changes, path=None):
    """把 {(section, key): value} 写回 ini 文件，只改这些行，注释和其它设置保持不动。"""
    import re
    path = Path(path or CONFIG_PATH)
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for (section, key), value in changes.items():
        start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
        if start is None:
            lines += ["", f"[{section}]", f"{key} = {value}"]
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        for i in range(start + 1, end):
            if re.match(rf"\s*{re.escape(key)}\s*=", lines[i]):
                lines[i] = f"{key} = {value}"
                break
        else:
            lines.insert(start + 1, f"{key} = {value}")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(lines) + newline)
    tmp.replace(path)
