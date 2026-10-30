"""电视答题屏保的测试：不连路由器、不碰电视。运行：.venv\\Scripts\\python -m unittest discover tests"""
import configparser
import json
import random
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from homecontrol import daemon, quiz
from homecontrol.quizserver import start_quiz_server


class MathGeneratorTest(unittest.TestCase):
    def test_every_question_has_four_distinct_options(self):
        gen = quiz.MathGenerator(random.Random(1))
        for difficulty in quiz.DIFFICULTIES:
            for grade in (7, 8, 9):
                for _ in range(400):
                    q = gen.generate(difficulty, grade)
                    self.assertEqual(len(set(q.options)), 4, q.stem)
                    self.assertIn(q.answer, range(4))
                    self.assertEqual(q.difficulty, difficulty)

    def test_marked_answers_are_mathematically_right(self):
        gen = quiz.MathGenerator(random.Random(2))
        sup = {"²": "**2", "³": "**3", "⁴": "**4"}
        checked = 0
        for _ in range(2000):
            q = gen.generate(random.Random(checked).choice(list(quiz.DIFFICULTIES)), 9)
            right = q.options[q.answer]
            if m := re.fullmatch(r"计算：(.+) = \?", q.stem):
                expr = m.group(1)
                expr = re.sub(r"\|(-?\d+)\|", r"abs(\1)", expr)
                expr = re.sub(r"-(\d)([²³⁴])", r"-(\1\2)", expr)  # -2² 是 -(2²)
                for k, v in sup.items():
                    expr = expr.replace(k, v)
                self.assertEqual(int(right), eval(expr), q.stem)
            elif m := re.fullmatch(r"解方程：(.+) = (-?\d+)，x = \?", q.stem):
                x = int(right)
                left = re.sub(r"(\d)x", r"\1*x", m.group(1)).replace("x", f"({x})")
                self.assertEqual(eval(left), int(m.group(2)), q.stem)
            elif m := re.fullmatch(r"解方程组：(.+)，(.+)", q.stem):
                x, y = map(int, re.findall(r"-?\d+", right))
                for eq in (m.group(1), m.group(2)):
                    lhs, rhs = eq.split(" = ")
                    lhs = re.sub(r"(\d)([xy])", r"\1*\2", lhs).replace("x", f"({x})").replace("y", f"({y})")
                    self.assertEqual(eval(lhs), int(rhs), q.stem)
            else:
                continue
            checked += 1
        self.assertGreater(checked, 500)


class GrundschulMatheTest(unittest.TestCase):
    def test_four_distinct_non_negative_options_for_every_grade(self):
        gen = quiz.GrundschulMathe(random.Random(5))
        for difficulty in quiz.DIFFICULTIES:
            for grade in (1, 2, 3, 4):
                for _ in range(300):
                    q = gen.generate(difficulty, grade)
                    self.assertEqual(len(set(q.options)), 4, q.stem)
                    self.assertFalse(any(o.startswith("-") for o in q.options), q.stem)
                    self.assertEqual(q.difficulty, difficulty)

    def test_marked_answers_are_right(self):
        gen = quiz.GrundschulMathe(random.Random(6))
        checked = 0
        for i in range(3000):
            q = gen.generate(random.Random(i).choice(list(quiz.DIFFICULTIES)), 4)
            right = q.options[q.answer]
            if m := re.fullmatch(r"([\d.() +−·:]+) = \?", q.stem):
                expr = m.group(1).replace(".", "").replace("−", "-").replace("·", "*")
                if ":" in expr:
                    a, b = map(int, expr.split(":"))
                    want = f"{a // b} Rest {a % b}" if a % b else quiz.de_num(a // b)
                    self.assertEqual(right, want, q.stem)
                else:
                    self.assertEqual(right, quiz.de_num(eval(expr)), q.stem)
            elif m := re.fullmatch(r"Runde ([\d.]+) auf (\w+)\.", q.stem):
                n = int(m.group(1).replace(".", ""))
                place = {"Zehner": 10, "Hunderter": 100, "Tausender": 1000}[m.group(2)]
                self.assertEqual(right, quiz.de_num((n + place // 2) // place * place), q.stem)
            elif m := re.search(r"hat (\d+) €.*für (\d+) € und .* für (\d+) €", q.stem):
                b, p1, p2 = map(int, m.groups())
                self.assertEqual(right, f"{b - p1 - p2} €", q.stem)
            else:
                continue
            checked += 1
        self.assertGreater(checked, 1500)


class GermanBankTest(unittest.TestCase):
    def test_bank_files_are_valid(self):
        bank = quiz.load_bank(quiz.DE_BANK_DIR)
        self.assertGreater(len(bank), 120)
        self.assertEqual({q.subject for q in bank}, set(quiz.LANGUAGES["de"]["subjects"]))
        self.assertEqual(len({q.id for q in bank}), len(bank))
        for q in bank:
            self.assertEqual(len(set(q.options)), 4, q.stem)
            self.assertIn(q.grade, (8, 9) if q.subject in ("franzoesisch", "englisch_alltag") else (1, 2, 3, 4))

    def test_bank_passes_checker(self):
        from tools.check_bank import check_bank
        problems, total = check_bank(quiz.DE_BANK_DIR)
        self.assertEqual(problems, [])
        self.assertGreater(total, 500)

    @unittest.skipUnless(shutil.which("node"), "需要 node 检查页面脚本语法")
    def test_quiz_page_script_parses(self):
        page = (Path(quiz.__file__).resolve().parent.parent / "assets" / "quiz.html").read_text(encoding="utf-8")
        script = page[page.index("<script>") + len("<script>"):page.rindex("</script>")]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "quiz.js"
            path.write_text(script, encoding="utf-8")
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hint_goes_out_before_answering_and_more_after(self):
        with tempfile.TemporaryDirectory() as d:
            item = {"id": "h1", "subject": "deutsch", "difficulty": "easy", "grade": 2, "type": "choice",
                    "stem": "?", "options": ["a", "b", "c", "d"], "answer": "A",
                    "hint": "Denk nach.", "explanation": "Weil.", "more": "Noch etwas."}
            (Path(d) / "deutsch.json").write_text(json.dumps([item]), encoding="utf-8")
            engine = quiz.QuizEngine(quiz.Rules(language="de", grade=4, subjects=["deutsch"]), d,
                                     rng=random.Random(1))
            q = engine.next_question("easy")
            public = q.public("de")
            self.assertEqual(public["hint"], "Denk nach.")
            self.assertNotIn("more", public)
            engine.pending[q.id] = (q, time.time() - 10)
            r = engine.answer(q.id, 0, 0, 0)
            self.assertEqual((r["explanation"], r["more"]), ("Weil.", "Noch etwas."))

    def test_engine_speaks_german_and_reloads_changed_bank(self):
        with tempfile.TemporaryDirectory() as d:
            item = {"id": "x1", "subject": "deutsch", "difficulty": "easy", "grade": 2, "type": "choice",
                    "stem": "Alt?", "options": ["a", "b", "c", "d"], "answer": "B"}
            path = Path(d) / "deutsch.json"
            path.write_text(json.dumps([item]), encoding="utf-8")
            rules = quiz.Rules(language="de", grade=4, subjects=["deutsch"])
            engine = quiz.QuizEngine(rules, d, rng=random.Random(1))
            self.assertEqual(engine.next_question("easy").stem, "Alt?")
            item.update(id="x2", stem="Neu?")
            path.write_text(json.dumps([item]), encoding="utf-8")
            q = engine.next_question("easy")
            self.assertEqual(q.stem, "Neu?")
            self.assertEqual(q.public("de")["subject"], "Deutsch")
            r = engine.answer(q.id, 0, 0, 0)
            self.assertTrue(r["message"].startswith("Leider falsch"))

    def test_config_switches_language(self):
        cfg = configparser.ConfigParser()
        cfg.read_dict({"quiz": {"language": "de"}})
        rules = quiz.Rules.from_config(cfg)
        self.assertEqual((rules.language, rules.grade), ("de", 4))
        self.assertEqual(rules.subjects, ["mathe", "deutsch", "sachunterricht", "englisch", "franzoesisch", "englisch_alltag"])

    def test_language_subjects_ignore_primary_grade_limit(self):
        rules = quiz.Rules(language="de", grade=4, subjects=["franzoesisch", "englisch_alltag"])
        engine = quiz.QuizEngine(rules, rng=random.Random(1))
        self.assertEqual(len(engine.static), 200)


class RulesTest(unittest.TestCase):
    rules = quiz.Rules()
    easy = quiz.Question("e", "english", "easy", "", ["a", "b", "c", "d"], 0)
    hard = quiz.Question("h", "math", "hard", "", ["a", "b", "c", "d"], 0)

    def test_reward_by_difficulty_and_daily_cap(self):
        self.assertEqual(quiz.evaluate(self.rules, self.hard, True, 10, 0, 0)[0], 8)
        self.assertEqual(quiz.evaluate(self.rules, self.hard, True, 10, 57, 0)[0], 3)
        self.assertEqual(quiz.evaluate(self.rules, self.hard, True, 10, 60, 0)[0], 0)

    def test_too_fast_earns_nothing(self):
        self.assertEqual(quiz.evaluate(self.rules, self.easy, True, 1, 0, 0)[0], 0)

    def test_only_easy_wrong_is_penalized_down_to_floor(self):
        self.assertEqual(quiz.evaluate(self.rules, self.hard, False, 10, 0, 0)[0], 0)
        self.assertEqual(quiz.evaluate(self.rules, self.easy, False, 10, 0, 0)[0], -2)
        self.assertEqual(quiz.evaluate(self.rules, self.easy, False, 10, 0, -29)[0], -1)
        self.assertEqual(quiz.evaluate(self.rules, self.easy, False, 10, 0, -30)[0], 0)


class EngineTest(unittest.TestCase):
    @unittest.skipUnless(quiz.DEFAULT_BANK_DIR.is_dir(), "中文题库（刷题解锁 App）不在本机")
    def test_app_bank_is_loaded_as_choice_questions(self):
        bank = quiz.load_bank(quiz.DEFAULT_BANK_DIR)
        self.assertGreater(len(bank), 40)
        for q in bank:
            self.assertEqual(len(q.options), 4)
            self.assertIn(q.answer, range(4))

    def test_answer_once_and_cooldown_after_three_wrong(self):
        engine = quiz.QuizEngine(quiz.Rules(), rng=random.Random(3))
        for _ in range(3):
            q = engine.next_question("medium")
            r = engine.answer(q.id, (q.answer + 1) % 4, 0, 0)
            self.assertFalse(r["correct"])
            self.assertIn("error", engine.answer(q.id, q.answer, 0, 0))  # 同一题不能再答
        self.assertGreater(r["cooldown"], 0)
        q = engine.next_question("medium")
        self.assertIn("error", engine.answer(q.id, q.answer, 0, 0))

    def test_public_question_hides_answer(self):
        q = quiz.QuizEngine(quiz.Rules()).next_question("easy").public()
        self.assertNotIn("answer", q)
        self.assertNotIn("explanation", q)


class FakeRouter:
    def __init__(self):
        self.blocked = {}

    def find(self, mac):
        return {"name": "TV", "ip": "192.0.2.25", "mac": mac, "online": True}

    def set_blocked(self, ip, blocked):
        self.blocked[ip] = blocked


def make_cfg():
    cfg = configparser.ConfigParser()
    cfg.read_string("""
[fritzbox]
address = fritz.box
user = u
password = p
[devices]
tv = 02:00:00:00:00:40
[limits]
daily_minutes = 60
track = tv
[quiz]
enabled = true
port = 0
""")
    return cfg


class WatcherQuizTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(daemon, "STATE", Path(self.tmp.name) / "usage.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.router = FakeRouter()
        self.watcher = daemon.Watcher(make_cfg(), self.router)

    def over_limit(self, minutes=61):
        state = daemon.load_state()
        state["minutes"]["tv"] = minutes
        daemon.save_state(state)
        return state

    def test_locks_when_time_is_up_and_unlocks_after_enough_correct_answers(self):
        state = self.over_limit()
        self.watcher.update_lock(state)
        daemon.save_state(state)
        self.assertTrue(state["tv_locked"])
        self.assertTrue(self.router.blocked["192.0.2.25"])

        # 答对一道困难题 +8 分钟：余额 -1 → 7，立即解锁并恢复上网
        q = self.watcher.engine.next_question("hard")
        self.watcher.engine.pending[q.id] = (q, time.time() - 10)
        r = self.watcher.quiz_answer(q.id, q.answer)
        self.assertTrue(r["correct"])
        self.assertEqual(r["state"]["remaining"], 7)
        self.assertFalse(r["state"]["locked"])
        self.assertFalse(self.router.blocked["192.0.2.25"])
        self.assertEqual(daemon.load_state()["quiz"]["earned"], 8)

    def test_switch_minutes_found_under_its_other_router_name(self):
        cfg = make_cfg()
        cfg.set("devices", "switch", "02:00:00:AA:BB:CC")
        cfg.set("limits", "track", "switch")
        router = FakeRouter()
        router.find = lambda mac: {"name": "PC-192-168-178-86", "ip": "192.0.2.86", "mac": mac, "online": True}
        router.list_devices = lambda: [
            {"name": "PC-192-168-178-86", "ip": "192.0.2.86", "mac": "02:11:11:AA:BB:CC", "online": True},
            {"name": "Kid-Switch", "ip": "192.0.2.66", "mac": "06:22:22:AA:BB:CC", "online": False},
            {"name": "Parent-iPad", "ip": "192.0.2.92", "mac": "06:22:22:DD:EE:FF", "online": True},
        ]
        w = daemon.Watcher(cfg, router)
        w.web.online_minutes_today = lambda: {"Kid-Switch": 17, "Parent-iPad": 236}
        state = daemon.load_state()
        w.tick(state)
        # 用 Kid-Switch 名下的真实上网时长，而不是「在线就算在用」；家长的 iPad 不算
        self.assertEqual(state["minutes"], {"switch": 17})

    def test_quiz_requests_answer_quickly_while_router_is_slow(self):
        import threading

        def slow_measure():
            time.sleep(2)  # 路由器 / FRITZ!Box 很慢
            return {"tv": ("inc", 1)}

        self.watcher.measure = slow_measure
        t = threading.Thread(target=self.watcher.cycle)
        t.start()
        time.sleep(0.3)
        start = time.time()
        self.watcher.quiz_state()
        self.watcher.quiz_question("easy")
        self.assertLess(time.time() - start, 0.5, "答题页请求被慢的路由器查询卡住了")
        t.join()
        self.assertEqual(daemon.load_state()["minutes"]["tv"], 1)

    def test_yesterdays_block_is_lifted_on_a_new_day(self):
        self.watcher.locked = True
        state = daemon.load_state()  # 新的一天：tv_locked 为 False
        self.watcher.update_lock(state)
        self.assertFalse(self.router.blocked["192.0.2.25"])

    def test_http_api_end_to_end(self):
        self.over_limit()
        server = start_quiz_server(self.watcher, 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if path.startswith("/api") else raw

        self.assertIn("答题解锁", call("/quiz"))
        self.assertEqual(call("/api/state")["remaining"], -1)
        q = call("/api/question", {"difficulty": "easy"})
        self.assertEqual(len(q["options"]), 4)
        r = call("/api/answer", {"id": q["id"], "choice": 0})
        self.assertIn("message", r)
        self.assertIn("state", r)
        before = self.watcher.last_heartbeat
        call("/api/heartbeat", {})
        self.assertGreater(self.watcher.last_heartbeat, before)


if __name__ == "__main__":
    unittest.main()
