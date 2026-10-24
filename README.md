# TV Quiz Lock · 电视答题解锁

[中文](#中文) · [English](#english)

## 中文

给三星 Tizen 电视做的防沉迷：每天看电视有时间上限，时间用完后电视断外网并弹出答题页，孩子答对题目赚回观看时间。

**工作原理**

1. 看门狗（`homecontrol/daemon.py`）每分钟从 FRITZ!Box 读取电视当天的上网时长。
2. 余额用完时，通过 FRITZ!Box TR-064 断掉电视外网，并用电视的 REST 接口启动电视答题应用（`tvapp/`）。
3. 电视答题应用跳转到本机提供的答题页（`assets/quiz.html`，`homecontrol/quizserver.py`，默认端口 8767）。
4. 答对加时间，简单题答错扣时间；余额回正后自动恢复上网。账本在 `data/usage.json`。

题库在 `assets/quiz_bank_de/*.json`（德国小学 1–4 年级：Mathe、Deutsch、Sachunterricht、Englisch），数学题另由 `GrundschulMathe` 自动生成。题库 JSON 改动后无需重启，下一题即生效。

**需要**

- FRITZ!Box（建一个有「FRITZ!Box 设置」权限的用户）
- 三星 Tizen 电视（开发者模式，用于安装答题应用）
- Python 3.11+；安装 Tizen 应用需要 Tizen Studio 和三星证书

**安装**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp config.example.ini config.local.ini   # 填写 FRITZ!Box 密码和电视 MAC
.venv/Scripts/python -m homecontrol devices   # 列出路由器里的设备，找电视 MAC
.venv/Scripts/python tools/build_tv_app.py --profile <证书配置名> --tv <电视IP>
.venv/Scripts/python -m homecontrol run       # 启动看门狗
```

Windows 下可用 `run_watcher.pyw` 无窗口开机自启。测试：`python -m unittest discover tests`。

## English

Screen-time limit for Samsung Tizen TVs. When the daily budget runs out, the TV loses internet access (via FRITZ!Box TR-064) and a quiz app takes over the screen. Correct answers earn viewing minutes back.

- `homecontrol/` – watcher, FRITZ!Box client, quiz engine and quiz web server
- `assets/quiz.html` – quiz page shown on the TV; `assets/quiz_bank_de/` – German primary-school question bank (grades 1–4)
- `tvapp/` – tiny Tizen web app that opens the quiz page; build and install with `tools/build_tv_app.py`

Setup: create a venv, `pip install -r requirements.txt`, copy `config.example.ini` to `config.local.ini`, fill in the router password and TV MAC, install the TV app, then run `python -m homecontrol run`.

Question bank format: each JSON file is a list of multiple-choice questions; see the existing files. Pull requests with new questions are welcome.

## License

MIT
