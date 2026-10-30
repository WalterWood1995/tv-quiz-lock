#!/data/data/com.termux/files/usr/bin/sh
# Always-on host on an Android tablet (Termux): keep the watcher running and pull
# new questions / code from GitHub every few hours. Started at boot by
# ~/.termux/boot/start-tvquiz (see README).
cd "$(dirname "$0")/.." || exit 1
termux-wake-lock 2>/dev/null
mkdir -p logs
PULL_EVERY=21600  # 秒；题库 JSON 拉下来立即生效，代码有变化才重启看门狗
BEAT_EVERY=300    # 秒；答题服务正常才上报心跳，整个 Termux 被杀时外部服务收不到就会发提醒
BEAT_URL=$(sed -n 's/^heartbeat_url *= *//p' config.local.ini 2>/dev/null | tr -d '\r')

pull() {
  git pull --ff-only -q >> logs/update.log 2>&1 || echo "$(date '+%F %T') git pull failed" >> logs/update.log
}

pull
# 手势页要在平板 Chrome 里常开（见 assets/hand）
(sleep 20; am start -a android.intent.action.VIEW -d http://127.0.0.1:8767/hand/ -n com.android.chrome/com.google.android.apps.chrome.Main >/dev/null 2>&1) &
while true; do
  python run_watcher.pyw &
  pid=$!
  last=$(date +%s)
  beat=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5  # 看门狗退出后几秒内就重启，电视上的答题页不会断太久
    if [ -n "$BEAT_URL" ] && [ $(( $(date +%s) - beat )) -ge $BEAT_EVERY ]; then
      beat=$(date +%s)
      curl -fs -m 5 -o /dev/null http://127.0.0.1:8767/api/state && curl -fs -m 10 -o /dev/null "$BEAT_URL"
    fi
    if [ $(( $(date +%s) - last )) -ge $PULL_EVERY ]; then
      last=$(date +%s)
      before=$(git rev-parse HEAD)
      pull
      if [ -n "$(git diff --name-only "$before" HEAD -- '*.py' '*.pyw')" ]; then
        echo "$(date '+%F %T') code updated, restarting watcher" >> logs/update.log
        kill "$pid"
      fi
    fi
  done
  sleep 5
done
