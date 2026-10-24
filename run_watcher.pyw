"""Started by the Windows scheduled task 'HomeControl Watcher' (no console window).
Output goes to logs/watcher.log."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
(ROOT / "logs").mkdir(exist_ok=True)
sys.stdout = sys.stderr = open(ROOT / "logs" / "watcher.log", "a", encoding="utf-8", buffering=1)

from homecontrol.__main__ import main  # noqa: E402

sys.exit(main(["run"]))
