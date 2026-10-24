#!/data/data/com.termux/files/usr/bin/sh
# Always-on host on an Android tablet (Termux): keep the watcher running and pull
# new questions / code from GitHub every few hours. Started at boot by
# ~/.termux/boot/start-tvquiz (see README).
cd "$(dirname "$0")/.." || exit 1
termux-wake-lock 2>/dev/null
mkdir -p logs
PULL_EVERY=21600  # 秒；题库 JSON 拉下来立即生效，代码有变化才重启看门狗

pull() {
  git pull --ff-only -q >> logs/update.log 2>&1 || echo "$(date '+%F %T') git pull failed" >> logs/update.log
}

pull
while true; do
  python run_watcher.pyw &
  pid=$!
  last=$(date +%s)
  while kill -0 "$pid" 2>/dev/null; do
    sleep 60
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
