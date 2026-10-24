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

**用旧安卓平板做常开主机**（电脑可以关机）

1. 装 [Termux](https://f-droid.org/packages/com.termux/) 和 [Termux:Boot](https://f-droid.org/packages/com.termux.boot/)（都从 F-Droid 装），打开一次 Termux:Boot。
2. 在 Termux 里：`pkg install python git python-cryptography`，然后 `git clone` 本仓库，`pip install fritzconnection samsungtvws websockets py3rijndael edge-tts`（先设 `AIOHTTP_NO_EXTENSIONS=1` 等环境变量可免编译）。
3. 放好 `config.local.ini`，新建 `~/.termux/boot/start-tvquiz`，内容为 `sh ~/tv-quiz-lock/tools/run_pad.sh`。
4. 系统设置里允许 Termux 后台运行、不受省电限制；平板一直插着电。路由器里给平板固定 IP，用 `tools/build_tv_app.py --host <平板IP>` 重装电视应用。

`tools/run_pad.sh` 保持看门狗运行，每 6 小时从 GitHub 拉一次新题和代码。

**题库格式**：每道题必填 `id, subject, difficulty, grade, type, stem, options, answer, explanation`，可选 `hint`（答题前的提示）和 `more`（讲解后引申的相关知识）。提交前运行 `python tools/check_bank.py`。

**答题页**：答对只显示一行字和彩纸；答错时卡通小机器人 Robo 滑出来，用语音讲解答案并引申相关知识（语音由主机用 edge-tts 合成，需要主机能上网）。孩子也可以按「Tipp」请 Robo 给提示，或答对后按「Erklär mal」听讲解。

## English

Screen-time limit for Samsung Tizen TVs. When the daily budget runs out, the TV loses internet access (via FRITZ!Box TR-064) and a quiz app takes over the screen. Correct answers earn viewing minutes back.

- `homecontrol/` – watcher, FRITZ!Box client, quiz engine and quiz web server
- `assets/quiz.html` – quiz page shown on the TV; `assets/quiz_bank_de/` – German primary-school question bank (grades 1–4)
- `tvapp/` – tiny Tizen web app that opens the quiz page; build and install with `tools/build_tv_app.py`

Setup: create a venv, `pip install -r requirements.txt`, copy `config.example.ini` to `config.local.ini`, fill in the router password and TV MAC, install the TV app, then run `python -m homecontrol run`.

Question bank format: each JSON file is a list of multiple-choice questions; see the existing files. Pull requests with new questions are welcome.

## License

MIT
