"""Build, sign and install the TV quiz app (tvapp/) on the Samsung TV.

Prerequisites (one-time):
  - Tizen Studio in C:\\tizen-studio (or set TIZEN_HOME), with the Samsung Certificate Extension
  - A Samsung certificate profile created in Certificate Manager (pass its name with --profile)
  - TV in Developer Mode with this PC's IP entered as Host PC IP

Usage:
  .venv\\Scripts\\python tools\\build_tv_app.py --profile <证书配置名> [--tv 192.168.178.25] [--no-install]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homecontrol.config import load_config  # noqa: E402
from homecontrol.daemon import QUIZ_PORT  # noqa: E402
from homecontrol.media import local_ip  # noqa: E402

TIZEN_HOME = Path(os.environ.get("TIZEN_HOME", r"C:\tizen-studio"))
TIZEN = TIZEN_HOME / "tools" / "ide" / "bin" / "tizen.bat"
SDB = TIZEN_HOME / "tools" / "sdb.exe"
APP_ID = "ShuaTiQz01.QuizLock"


def run(*cmd, cwd=None):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    # Tizen 的 .bat 工具会调用同目录下的脚本：子进程里不能带 NoDefaultCurrentDirectoryInExePath
    env = {k: v for k, v in os.environ.items() if k.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"}
    out = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=cwd, env=env)
    print(out.stdout.strip(), out.stderr.strip(), sep="\n", flush=True)
    if out.returncode:
        raise SystemExit(f"命令失败（{out.returncode}）")
    return out.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="Certificate Manager 里的证书配置名")
    ap.add_argument("--tv", default="192.168.178.25")
    ap.add_argument("--no-install", action="store_true")
    args = ap.parse_args()
    for tool in (TIZEN, SDB):
        if not tool.exists():
            raise SystemExit(f"找不到 {tool}，请先安装 Tizen Studio")

    cfg = load_config()
    port = cfg.getint("quiz", "port", fallback=QUIZ_PORT)
    url = f"http://{local_ip(args.tv)}:{port}/quiz"

    build = Path(tempfile.mkdtemp()) / "QuizLock"
    shutil.copytree(ROOT / "tvapp", build)
    index = build / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("__QUIZ_URL__", url), encoding="utf-8")
    print(f"答题页地址: {url}")

    # 必须在应用目录里运行，否则 tizen CLI 报 Invalid argument
    run(TIZEN, "package", "-t", "wgt", "-s", args.profile, "--", build, cwd=build)
    # 包名取自 config.xml 的中文名，命令行传中文文件名不可靠，改成英文
    wgt = next(build.glob("*.wgt")).rename(build / "QuizLock.wgt")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    shutil.copy2(wgt, dist / "QuizLock.wgt")
    print(f"已打包: {dist / 'QuizLock.wgt'}")
    if args.no_install:
        return

    run(SDB, "connect", args.tv)
    serial = f"{args.tv}:26101"
    run(TIZEN, "install", "-n", wgt.name, "-s", serial, "--", build, cwd=build)
    print(f"已安装到电视。应用 ID: {APP_ID}（写进 config.local.ini 的 [quiz] tv_app）")


if __name__ == "__main__":
    main()
