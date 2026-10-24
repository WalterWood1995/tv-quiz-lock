"""Check the quiz bank JSON files before they go live (also run by the weekly question job).

Usage:
  python tools/check_bank.py [bank_dir]     (default: assets/quiz_bank_de)
Exit code 1 and a list of problems if anything is wrong.
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBJECTS = {"mathe", "deutsch", "sachunterricht", "englisch", "math", "english", "chinese"}
DIFFICULTIES = {"easy", "medium", "hard"}
REQUIRED = ("id", "subject", "difficulty", "grade", "type", "stem", "options", "answer", "explanation")


def check_question(q, where):
    problems = []
    missing = [k for k in REQUIRED if k not in q]
    if missing:
        return [f"{where}: fehlt {', '.join(missing)}"]
    if q["type"] != "choice":
        problems.append(f"{where}: type muss 'choice' sein")
    if q["subject"] not in SUBJECTS:
        problems.append(f"{where}: unbekanntes Fach {q['subject']!r}")
    if q["difficulty"] not in DIFFICULTIES:
        problems.append(f"{where}: unbekannte Stufe {q['difficulty']!r}")
    if not isinstance(q["grade"], int) or not 1 <= q["grade"] <= 9:
        problems.append(f"{where}: grade {q['grade']!r}")
    opts = q["options"]
    if not isinstance(opts, list) or len(opts) != 4 or not all(isinstance(o, str) and o.strip() for o in opts):
        problems.append(f"{where}: genau 4 nicht-leere Optionen nötig")
    # 不忽略大小写：德语题常考大小写本身
    elif len({o.strip() for o in opts}) != 4:
        problems.append(f"{where}: doppelte Optionen {opts}")
    if q["answer"] not in ("A", "B", "C", "D"):
        problems.append(f"{where}: answer {q['answer']!r}")
    for key in ("stem", "explanation"):
        if not isinstance(q[key], str) or not q[key].strip():
            problems.append(f"{where}: {key} leer")
    more = q.get("more")
    if more is not None and (not isinstance(more, str) or not more.strip()):
        problems.append(f"{where}: more leer")
    hint = q.get("hint")
    if hint is not None:
        if not isinstance(hint, str) or not hint.strip():
            problems.append(f"{where}: hint leer")
        elif isinstance(opts, list) and q["answer"] in ("A", "B", "C", "D") and len(opts) == 4:
            right = opts[ord(q["answer"]) - ord("A")].strip().lower()
            # 提示不能直接说出答案（短答案如数字容易误判，只查 4 个字符以上的）
            if len(right) >= 4 and right in hint.lower():
                problems.append(f"{where}: hint verrät die Antwort")
    return problems


def check_bank(bank_dir):
    problems, ids, letters, total = [], collections.Counter(), collections.Counter(), 0
    files = sorted(Path(bank_dir).glob("*.json"))
    if not files:
        return [f"keine JSON-Dateien in {bank_dir}"], 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            problems.append(f"{path.name}: kein gültiges JSON ({e})")
            continue
        if not isinstance(data, list):
            problems.append(f"{path.name}: muss eine Liste sein")
            continue
        for i, q in enumerate(data):
            where = f"{path.name}[{i}] {q.get('id', '?') if isinstance(q, dict) else '?'}"
            if not isinstance(q, dict):
                problems.append(f"{where}: kein Objekt")
                continue
            problems += check_question(q, where)
            ids[q.get("id")] += 1
            letters[q.get("answer")] += 1
            total += 1
    problems += [f"doppelte id {i!r} ({n}×)" for i, n in ids.items() if n > 1]
    # 正确答案不能老是同一个字母
    if total >= 40:
        for letter in "ABCD":
            if letters[letter] < total * 0.15:
                problems.append(f"Antwort {letter} nur {letters[letter]}× von {total} – Antworten besser mischen")
    return problems, total


def main():
    bank_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "assets" / "quiz_bank_de"
    problems, total = check_bank(bank_dir)
    for p in problems:
        print(p)
    print(f"{total} Fragen, {len(problems)} Probleme")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
