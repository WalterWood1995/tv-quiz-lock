"""电视答题屏保：题库、出题和奖惩规则（与「刷题解锁」手机 App 的规则一致）。

电视上只用遥控器作答，所以全部是四选一：
- 数学题由生成器现出（数量无限、答案精确），填空题也转换成选择题；
- 其他科目读题库目录里的 JSON（格式同「刷题解锁」App 的题库），只取其中的选择题。

两套内容，由 [quiz] language 选择：
- zh：中国初中（数学、英语、语文/常识），题库读 App 的免费题库；
- de：德国小学 1–4 年级（Mathe、Deutsch、Sachunterricht、Englisch），题库在 assets/quiz_bank_de。

题库文件改了不用重启：每次出题前看一眼文件有没有变，变了就重新读。
"""
import json
import random
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BANK_DIR = Path.home() / "Documents" / "防手机沉迷APP" / "apps" / "child_app" / "assets" / "question_bank"
DE_BANK_DIR = Path(__file__).resolve().parent.parent / "assets" / "quiz_bank_de"

SUBJECTS = {"math": "数学", "english": "英语", "chinese": "语文/常识"}
DIFFICULTIES = {"easy": "简单", "medium": "中等", "hard": "困难"}

LANGUAGES = {
    "zh": {
        "subjects": SUBJECTS, "difficulties": DIFFICULTIES, "math": "math", "bank": DEFAULT_BANK_DIR, "grade": 7,
        "msg": {
            "too_fast": "答得太快了，认真读题哦，这题不计时间。",
            "correct_plus": "答对了！+{d} 分钟",
            "correct_capped": "答对了！但今天答题加时已经到上限了。",
            "wrong_free": "答错了，这档题不扣时间，看看讲解再试试。",
            "wrong_minus": "答错了，-{d} 分钟",
            "wrong": "答错了。",
            "expired": "题目已过期，请换一题",
            "cooldown": "先看看讲解，{s} 秒后再继续",
            "streak": " 连续答错 3 题了，先看讲解，1 分钟后再继续。",
        },
    },
    "de": {
        "subjects": {"mathe": "Mathe", "deutsch": "Deutsch", "sachunterricht": "Sachunterricht", "englisch": "Englisch",
                     "franzoesisch": "Französisch", "englisch_alltag": "Englisch Alltag"},
        # 外语口语按德国 8–9 年级进度，不受小学年级 grade 限制
        "subject_grades": {"franzoesisch": 9, "englisch_alltag": 9},
        "difficulties": {"easy": "Leicht", "medium": "Mittel", "hard": "Schwer"},
        "math": "mathe", "bank": DE_BANK_DIR, "grade": 4,
        "msg": {
            "too_fast": "Das ging zu schnell – lies die Frage genau. Diese Aufgabe zählt nicht.",
            "correct_plus": "Richtig! +{d} Minuten",
            "correct_capped": "Richtig! Aber das Bonus-Limit für heute ist erreicht.",
            "wrong_free": "Leider falsch. Auf dieser Stufe gibt es keinen Abzug – lies die Erklärung.",
            "wrong_minus": "Leider falsch, -{d} Minuten",
            "wrong": "Leider falsch.",
            "expired": "Diese Frage ist abgelaufen, bitte nimm eine neue.",
            "cooldown": "Lies zuerst die Erklärung, in {s} Sekunden geht es weiter.",
            "streak": " Drei Fehler hintereinander – lies die Erklärung, in 1 Minute geht es weiter.",
        },
    },
}
SUPERSCRIPT = {2: "²", 3: "³", 4: "⁴"}


@dataclass
class Question:
    id: str
    subject: str
    difficulty: str
    stem: str
    options: list
    answer: int  # 正确选项的下标
    explanation: str = ""
    grade: int = 7
    hint: str = ""
    more: str = ""  # 讲解后再引申的相关知识点

    def public(self, lang="zh"):
        """发给电视的版本：不含答案。"""
        texts = LANGUAGES[lang]
        return {"id": self.id, "subject": texts["subjects"].get(self.subject, self.subject),
                "difficulty": self.difficulty, "difficultyLabel": texts["difficulties"][self.difficulty],
                "stem": self.stem, "options": self.options, "hint": self.hint}


def bank_files(bank_dir):
    path = Path(bank_dir)
    return sorted(path.glob("*.json")) if path.is_dir() else []


def load_bank(bank_dir, subjects=None):
    """读取题库目录里所有 JSON 的选择题；目录不存在时只出数学题。"""
    questions = []
    for path in bank_files(bank_dir):
        for q in json.loads(path.read_text(encoding="utf-8-sig")):
            if q["type"] != "choice" or (subjects is not None and q["subject"] not in subjects):
                continue
            questions.append(Question(q["id"], q["subject"], q["difficulty"], q["stem"], q["options"],
                                      ord(q["answer"]) - ord("A"), q.get("explanation", ""), q.get("grade", 7),
                                      q.get("hint", ""), q.get("more", "")))
    return questions


class MathGenerator:
    """初中数学题生成器，全部输出为四选一。"""

    TRIPLES = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (6, 8, 10), (9, 12, 15)]

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def generate(self, difficulty, grade=7):
        pool = {
            "easy": [self._rational_sum, self._absolute_value, self._power_sign],
            "medium": [self._linear_equation, self._evaluate] + ([self._pythagorean] if grade >= 8 else []),
            "hard": [self._linear_system] + ([self._slope, self._factorization] if grade >= 8 else [])
                    + ([self._quadratic] if grade >= 9 else []),
        }[difficulty]
        return self.rng.choice(pool)()

    def _int(self, lo, hi):
        return self.rng.randint(lo, hi)

    def _nonzero(self, lo, hi):
        while True:
            v = self._int(lo, hi)
            if v:
                return v

    @staticmethod
    def _p(n):
        return f"({n})" if n < 0 else str(n)

    def _choices(self, correct, distractors, fmt=str):
        """正确答案 + 干扰项凑成 4 个不同选项并打乱，返回 (选项文本, 正确下标)。"""
        opts = [correct]
        for d in distractors:
            if len(opts) == 4:
                break
            if d not in opts:
                opts.append(d)
        step = 1
        while len(opts) < 4:
            for cand in (correct + step, correct - step):
                if len(opts) < 4 and cand not in opts:
                    opts.append(cand)
            step += 1
        self.rng.shuffle(opts)
        return [fmt(o) for o in opts], opts.index(correct)

    def _q(self, qid, difficulty, stem, correct, distractors, explanation, fmt=str):
        options, answer = self._choices(correct, distractors, fmt)
        return Question(qid, "math", difficulty, stem, options, answer, explanation)

    def _rational_sum(self):
        a, b = self._nonzero(-20, 20), self._nonzero(-20, 20)
        sub = self.rng.random() < 0.5
        result = a - b if sub else a + b
        op = "-" if sub else "+"
        return self._q(f"gen-math-rs-{a}{op}{b}", "easy", f"计算：{self._p(a)} {op} {self._p(b)} = ?", result,
                       [a + b if sub else a - b, -result, result + 10],
                       f"减去一个数等于加上它的相反数：{self._p(a)} + {self._p(-b)} = {result}。" if sub
                       else f"同号相加取相同符号，异号相加用绝对值较大的符号：结果为 {result}。")

    def _absolute_value(self):
        a, b = self._nonzero(-15, 15), self._nonzero(-15, 15)
        result = abs(a) - abs(b)
        return self._q(f"gen-math-abs-{a}-{b}", "easy", f"计算：|{a}| - |{b}| = ?", result,
                       [a - b, abs(a - b), abs(a) + abs(b)],
                       f"|{a}| = {abs(a)}，|{b}| = {abs(b)}，所以结果为 {abs(a)} - {abs(b)} = {result}。")

    def _power_sign(self):
        base, exp = self._int(2, 5), self._int(2, 4)
        neg_inside = self.rng.random() < 0.5
        mag = base ** exp
        result = (-mag if exp % 2 else mag) if neg_inside else -mag
        expr = f"(-{base}){SUPERSCRIPT[exp]}" if neg_inside else f"-{base}{SUPERSCRIPT[exp]}"
        why = (f"{expr} 表示 {exp} 个 (-{base}) 相乘，指数为{'奇数结果为负' if exp % 2 else '偶数结果为正'}，等于 {result}。"
               if neg_inside else f"{expr} 表示 {base}{SUPERSCRIPT[exp]} 的相反数，等于 {result}。")
        return self._q(f"gen-math-pow-{expr}", "easy", f"计算：{expr} = ?", result,
                       [-result, base * exp, -base * exp], why)

    @staticmethod
    def _coef(a, var):
        return var if a == 1 else f"-{var}" if a == -1 else f"{a}{var}"

    def _linear_equation(self):
        x, a, b = self._int(-10, 10), self._nonzero(-9, 9), self._int(-20, 20)
        c = a * x + b
        left = self._coef(a, "x") + (f" {'+' if b > 0 else '-'} {abs(b)}" if b else "")
        return self._q(f"gen-math-le-{a}-{b}-{c}", "medium", f"解方程：{left} = {c}，x = ?", x,
                       [-x, x + 1, c - b],
                       f"移项：{a}x = {c - b}，两边同除以 {a}，得 x = {x}。")

    def _evaluate(self):
        a, b, c = self._nonzero(-6, 6), self._nonzero(-6, 6), self._int(2, 3)
        result = a * b - a ** c
        return self._q(f"gen-math-ev-{a}-{b}-{c}", "medium",
                       f"当 a = {a}，b = {b} 时，求 ab - a{SUPERSCRIPT[c]} 的值。", result,
                       [a * b + a ** c, -result, a * b - abs(a) ** c],
                       f"ab = {a * b}；a{SUPERSCRIPT[c]} = {self._p(a)}{SUPERSCRIPT[c]} = {a ** c}；"
                       f"结果为 {a * b} - {self._p(a ** c)} = {result}。")

    def _pythagorean(self):
        t = self.rng.choice(self.TRIPLES)
        hyp = self.rng.random() < 0.5
        if hyp:
            stem, ans = f"直角三角形两条直角边分别为 {t[0]} 和 {t[1]}，斜边长为多少？", t[2]
            why = f"c² = {t[0]}² + {t[1]}² = {t[2] ** 2}，c = {t[2]}。"
        else:
            stem, ans = f"直角三角形斜边为 {t[2]}，一条直角边为 {t[0]}，另一条直角边长为多少？", t[1]
            why = f"b² = {t[2]}² - {t[0]}² = {t[1] ** 2}，b = {t[1]}。"
        return self._q(f"gen-math-py-{t}-{hyp}", "medium", stem, ans, [t[0] + t[1], ans + 1, t[2] - t[0]], why)

    def _linear_system(self):
        x, y = self._int(-6, 6), self._int(-6, 6)
        while True:
            a1, b1, a2, b2 = (self._nonzero(-5, 5) for _ in range(4))
            if a1 * b2 - a2 * b1:
                break
        c1, c2 = a1 * x + b1 * y, a2 * x + b2 * y

        def eq(a, b, c):
            return f"{self._coef(a, 'x')} {'+' if b > 0 else '-'} {'' if abs(b) == 1 else abs(b)}y = {c}"

        correct = (x, y)
        # x、y 为 0 或相等时前几个会重复，后面的保证总能凑够 3 个不同干扰项
        distractors = [(y, x), (-x, y), (x, -y), (-x, -y), (x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)]
        return self._q(f"gen-math-ls-{a1}-{b1}-{c1}-{a2}-{b2}-{c2}", "hard",
                       f"解方程组：{eq(a1, b1, c1)}，{eq(a2, b2, c2)}", correct, distractors,
                       f"用加减消元法消去 x，得 y = {y}；代回得 x = {x}。",
                       fmt=lambda p: f"x = {p[0]}，y = {p[1]}")

    def _slope(self):
        k, b = self._nonzero(-5, 5), self._int(-8, 8)
        x1 = self._int(-5, 5)
        x2 = x1
        while x2 == x1:
            x2 = self._int(-5, 5)
        y1, y2 = k * x1 + b, k * x2 + b
        return self._q(f"gen-math-sl-{x1}-{y1}-{x2}-{y2}", "hard",
                       f"一次函数 y = kx + b 的图像经过点 ({x1}, {y1}) 和 ({x2}, {y2})，b = ?", b,
                       [-b, k, b + k],
                       f"k = ({y2} - {self._p(y1)}) / ({x2} - {self._p(x1)}) = {k}；代入 ({x1}, {y1}) 得 b = {b}。")

    @staticmethod
    def _term(v, suffix):
        if v == 0:
            return ""
        mag = "" if abs(v) == 1 and suffix else str(abs(v))
        return f" {'+' if v > 0 else '-'} {mag}{suffix}"

    def _factorization(self):
        p, q = self._nonzero(-9, 9), self._nonzero(-9, 9)
        m = max(p, q)
        return self._q(f"gen-math-fa-{p}-{q}", "hard",
                       f"已知 x²{self._term(p + q, 'x')}{self._term(p * q, '')} = (x + m)(x + n)，且 m ≥ n，m = ?",
                       m, [min(p, q), -m, p + q],
                       f"找两个数，乘积为 {p * q}，和为 {p + q}：是 {p} 和 {q}，所以 m = {m}。")

    def _quadratic(self):
        r1, r2 = self._int(-9, 9), self._int(-9, 9)
        big = max(r1, r2)
        return self._q(f"gen-math-qr-{r1}-{r2}", "hard",
                       f"方程 x²{self._term(-(r1 + r2), 'x')}{self._term(r1 * r2, '')} = 0 的较大的根是？", big,
                       [min(r1, r2), -big, r1 + r2],
                       f"因式分解得两根 {r1} 和 {r2}，较大的根是 {big}。")


def de_num(n):
    """德语的千位分隔符是点：12.500"""
    return f"{n:,}".replace(",", ".")


class GrundschulMathe(MathGenerator):
    """德国小学数学（Klasse 1–4）出题器，题目和讲解都是德语，全部四选一。"""

    LIMIT = {1: 20, 2: 100, 3: 1000, 4: 10000}
    NAMES = ["Lena", "Tim", "Mia", "Paul", "Emma", "Ben", "Lina", "Noah", "Hannah", "Felix"]
    ITEMS = [("ein Heft", 1, 3), ("einen Stift", 1, 3), ("ein Buch", 5, 12), ("einen Ball", 4, 9),
             ("ein Eis", 1, 3), ("eine Brezel", 1, 2), ("ein Puzzle", 6, 15), ("eine Trinkflasche", 4, 8)]

    def generate(self, difficulty, grade=4):
        self.grade = g = min(max(grade, 1), 4)
        pool = {
            "easy": [self._plus_minus] + ([self._times_table] if g >= 2 else []),
            "medium": [self._missing_number, self._double_half]
                      + ([self._divide_table] if g >= 2 else []) + ([self._divide_rest] if g >= 3 else []),
            "hard": [self._money_story] + ([self._order_of_ops, self._multiply] if g >= 3 else [self._three_terms])
                    + ([self._round, self._divide_big] if g >= 4 else []),
        }[difficulty]
        return self.rng.choice(pool)()

    def _limit(self):
        return self.LIMIT[self.grade]

    def _choices(self, correct, distractors, fmt=str):
        # 小学不出负数：去掉负的干扰项，不够时往大的方向补
        usable = [d for d in distractors if not isinstance(d, int) or d >= 0]
        if isinstance(correct, int):
            usable += [correct + step for step in range(1, 5)]
        return super()._choices(correct, usable, fmt)

    def _plus_minus(self):
        limit = self._limit()
        if self.rng.random() < 0.5:
            a = self._int(1, limit - 1)
            b = self._int(1, limit - a)
            r, op, other = a + b, "+", abs(a - b)
        else:
            a = self._int(2, limit)
            b = self._int(1, a - 1)
            r, op, other = a - b, "−", a + b
        return self._q(f"gen-de-pm-{a}{op}{b}", "easy", f"{de_num(a)} {op} {de_num(b)} = ?", r,
                       [r + 10, r - 10, other, r + 1, r - 1], f"{de_num(a)} {op} {de_num(b)} = {de_num(r)}",
                       fmt=de_num)

    def _times_table(self):
        a, b = self._int(2, 10), self._int(1, 10)
        r = a * b
        return self._q(f"gen-de-mal-{a}x{b}", "easy", f"{a} · {b} = ?", r,
                       [a * (b + 1), (a + 1) * b, a + b, a * (b - 1)],
                       f"Einmaleins: {b}-mal die {a} ist {r}.")

    def _missing_number(self):
        limit = self._limit()
        a = self._int(1, limit // 2)
        b = self._int(1, limit // 2)
        c = a + b
        return self._q(f"gen-de-luecke-{a}-{c}", "medium", f"{de_num(a)} + ___ = {de_num(c)}", b,
                       [c + a, b + 1, b - 1, b + 10],
                       f"Rechne rückwärts: {de_num(c)} − {de_num(a)} = {de_num(b)}.", fmt=de_num)

    def _double_half(self):
        n = self._int(2, self._limit() // 2)
        if self.rng.random() < 0.5:
            return self._q(f"gen-de-dop-{n}", "medium", f"Verdopple {de_num(n)}.", 2 * n,
                           [n + 2, 2 * n + 1, 2 * n - 1, 2 * n + 10],
                           f"Verdoppeln heißt mal 2: {de_num(n)} + {de_num(n)} = {de_num(2 * n)}.", fmt=de_num)
        m = 2 * n
        return self._q(f"gen-de-halb-{m}", "medium", f"Halbiere {de_num(m)}.", n,
                       [m, n + 1, n - 1, n + 10],
                       f"Halbieren heißt durch 2: {de_num(n)} + {de_num(n)} = {de_num(m)}.", fmt=de_num)

    def _divide_table(self):
        b, q = self._int(2, 10), self._int(1, 10)
        a = b * q
        return self._q(f"gen-de-div-{a}:{b}", "medium", f"{a} : {b} = ?", q, [q + 1, q - 1, b, a - b],
                       f"Umkehraufgabe: {q} · {b} = {a}, also {a} : {b} = {q}.")

    def _divide_rest(self):
        b, q = self._int(3, 9), self._int(2, 10)
        r = self._int(1, b - 1)
        a = b * q + r
        other_r = r + 1 if r + 1 < b else r - 1
        return self._q(f"gen-de-rest-{a}:{b}", "medium", f"{a} : {b} = ?", (q, r),
                       [(q + 1, r), (q - 1, r), (q, other_r)],
                       f"{q} · {b} = {b * q}, es bleiben {a} − {b * q} = {r} übrig.",
                       fmt=lambda p: f"{p[0]} Rest {p[1]}" if p[1] else str(p[0]))

    def _three_terms(self):
        limit = self._limit()
        a = self._int(1, limit - 2)
        b = self._int(1, limit - a)
        c = self._int(1, a + b)
        r = a + b - c
        return self._q(f"gen-de-drei-{a}+{b}-{c}", "hard", f"{a} + {b} − {c} = ?", r,
                       [a + b + c, r + 1, r - 1, r + 10],
                       f"Von links nach rechts: {a} + {b} = {a + b}, dann {a + b} − {c} = {r}.")

    def _order_of_ops(self):
        a, b, c = self._int(2, 30), self._int(2, 9), self._int(2, 9)
        if self.rng.random() < 0.5:
            r = a + b * c
            return self._q(f"gen-de-pvs-{a}+{b}x{c}", "hard", f"{a} + {b} · {c} = ?", r,
                           [(a + b) * c, a + b + c, r + 10],
                           f"Punkt vor Strich: zuerst {b} · {c} = {b * c}, dann {a} + {b * c} = {r}.")
        r = (a + b) * c
        return self._q(f"gen-de-kl-{a}+{b}x{c}", "hard", f"({a} + {b}) · {c} = ?", r,
                       [a + b * c, a * c + b, r + 10],
                       f"Klammer zuerst: {a} + {b} = {a + b}, dann {a + b} · {c} = {r}.")

    def _multiply(self):
        a = self._int(100, 999) if self.grade >= 4 else self._int(12, 99)
        b = self._int(2, 9)
        r = a * b
        h, rest = a // 10 * 10, a % 10
        return self._q(f"gen-de-mul-{a}x{b}", "hard", f"{de_num(a)} · {b} = ?", r,
                       [r + 10, r - 10, a * (b + 1), h * b],
                       f"Zerlegen: {de_num(h)} · {b} = {de_num(h * b)} und {rest} · {b} = {rest * b}; "
                       f"zusammen {de_num(r)}.", fmt=de_num)

    def _divide_big(self):
        b, q = self._int(2, 9), self._int(12, 250)
        a = b * q
        return self._q(f"gen-de-gdiv-{a}:{b}", "hard", f"{de_num(a)} : {b} = ?", q, [q + 1, q - 1, q + 10, q * 10],
                       f"Probe: {de_num(q)} · {b} = {de_num(a)}.", fmt=de_num)

    def _round(self):
        n = self._int(1000, 99999)
        place, name = self.rng.choice([(10, "Zehner"), (100, "Hunderter"), (1000, "Tausender")])
        down = n // place * place
        r = (n + place // 2) // place * place
        digit = n // (place // 10) % 10
        other = down + place if r == down else down  # 往另一个方向取整
        why = (f"Die Ziffer rechts daneben ist {digit}: bei 0–4 wird abgerundet, bei 5–9 aufgerundet. "
               f"{de_num(n)} ≈ {de_num(r)}.")
        return self._q(f"gen-de-rund-{n}-{place}", "hard", f"Runde {de_num(n)} auf {name}.", r,
                       [other, r + place, r - place, n // (place * 10) * place * 10], why, fmt=de_num)

    def _money_story(self):
        name = self.rng.choice(self.NAMES)
        (i1, lo1, hi1), (i2, lo2, hi2) = self.rng.sample(self.ITEMS, 2)
        p1, p2 = self._int(lo1, hi1), self._int(lo2, hi2)
        budget = self.rng.choice([b for b in (10, 20, 50) if b > p1 + p2])
        r = budget - p1 - p2
        return self._q(f"gen-de-geld-{name}-{budget}-{p1}-{p2}", "hard",
                       f"{name} hat {budget} €. {name} kauft {i1} für {p1} € und {i2} für {p2} €. "
                       f"Wie viel Geld bleibt übrig?", r, [p1 + p2, budget - p1, r + 1, r + 2],
                       f"{p1} € + {p2} € = {p1 + p2} €; {budget} € − {p1 + p2} € = {r} €.",
                       fmt=lambda v: f"{v} €")


@dataclass
class Rules:
    reward: dict = field(default_factory=lambda: {"easy": 2, "medium": 4, "hard": 8})
    penalty_easy: int = 2
    daily_earn_cap: int = 60
    min_balance: int = -30
    min_answer_seconds: int = 3
    grade: int = 7
    subjects: list = field(default_factory=lambda: ["math", "english", "chinese"])
    language: str = "zh"

    @classmethod
    def from_config(cls, cfg):
        if not cfg.has_section("quiz"):
            return cls()
        q, d = cfg["quiz"], cls()
        lang = q.get("language", "zh").strip()
        if lang not in LANGUAGES:
            lang = "zh"
        known = LANGUAGES[lang]["subjects"]
        return cls(
            reward={k: q.getint(f"reward_{k}", d.reward[k]) for k in d.reward},
            penalty_easy=q.getint("penalty_easy", d.penalty_easy),
            daily_earn_cap=q.getint("daily_earn_cap", d.daily_earn_cap),
            min_balance=q.getint("min_balance", d.min_balance),
            min_answer_seconds=q.getint("min_answer_seconds", d.min_answer_seconds),
            grade=q.getint("grade", LANGUAGES[lang]["grade"]),
            subjects=[s.strip() for s in q.get("subjects", ",".join(known)).split(",") if s.strip() in known]
                     or list(known),
            language=lang,
        )


def evaluate(rules, question, correct, elapsed, earned_today, remaining):
    """返回 (分钟变化, 提示语)。正数为奖励，负数为扣除。只有简单题答错才扣。"""
    msg = LANGUAGES[rules.language]["msg"]
    if correct:
        if elapsed < rules.min_answer_seconds:
            return 0, msg["too_fast"]
        room = max(0, rules.daily_earn_cap - earned_today)
        delta = min(rules.reward[question.difficulty], room)
        return (delta, msg["correct_plus"].format(d=delta)) if delta else (0, msg["correct_capped"])
    if question.difficulty != "easy":
        return 0, msg["wrong_free"]
    deduct = min(rules.penalty_easy, max(0, remaining - rules.min_balance))
    return (-deduct, msg["wrong_minus"].format(d=deduct)) if deduct else (0, msg["wrong"])


class QuizEngine:
    """出题、判题、防重复、连错冷却。时间账本由调用方（Watcher）通过 apply 回调更新。"""

    COOLDOWN = 60
    WRONG_LIMIT = 3

    def __init__(self, rules, bank_dir=None, rng=None):
        self.rules = rules
        self.lang = LANGUAGES[rules.language]
        self.rng = rng or random.Random()
        self.bank_dir = Path(bank_dir) if bank_dir else self.lang["bank"]
        self.bank_signature = None
        self.static = []
        self.reload_bank()
        self.math = (GrundschulMathe if rules.language == "de" else MathGenerator)(self.rng)
        self.recent = set()
        self.pending = {}  # id -> (Question, 出题时间)
        self.wrong_streak = 0
        self.cooldown_until = 0

    def reload_bank(self):
        """题库文件有变化（增删改）就重新读；返回是否重新读了。文件写到一半读不了时先用旧题库。"""
        files = bank_files(self.bank_dir)
        # 用内容校验和而不是修改时间：有的文件系统（如安卓）时间精度粗，连着改两次会看不出变化
        signature = [(f.name, zlib.crc32(f.read_bytes())) for f in files]
        if signature == self.bank_signature:
            return False
        try:
            bank = load_bank(self.bank_dir, self.rules.subjects)
        except (ValueError, KeyError, OSError):
            return False
        limits = self.lang.get("subject_grades", {})
        self.static = [q for q in bank if q.grade <= limits.get(q.subject, self.rules.grade)]
        self.bank_signature = signature
        return True

    def cooling_down(self):
        return max(0, int(self.cooldown_until - time.time()))

    def next_question(self, difficulty):
        if difficulty not in DIFFICULTIES:
            difficulty = "medium"
        self.reload_bank()
        math = self.lang["math"]
        # 这一档在题库里有题的科目才出；数学总能现出
        have = {s.subject for s in self.static if s.difficulty == difficulty}
        subjects = [s for s in self.rules.subjects if s == math or s in have] or [math]
        subject = self.rng.choice(subjects)
        q = None
        # 题库里也有数学题（看钟、钱、图形）时，数学有三成机会出题库里的
        if subject != math or (subject in have and self.rng.random() < 0.3):
            pool = [s for s in self.static if s.subject == subject and s.difficulty == difficulty
                    and s.id not in self.recent] or [s for s in self.static if s.subject == subject
                                                   and s.difficulty == difficulty]
            q = self.rng.choice(pool) if pool else None
        if q is None:
            q = self.math.generate(difficulty, self.rules.grade)
            for _ in range(10):
                if q.id not in self.recent:
                    break
                q = self.math.generate(difficulty, self.rules.grade)
        self.recent.add(q.id)
        self.pending[q.id] = (q, time.time())
        return q

    def answer(self, qid, choice, earned_today, remaining):
        """判题。返回 dict；delta 是对时间余额的分钟变化，由调用方记账。"""
        if qid not in self.pending:
            return {"error": self.lang["msg"]["expired"]}
        if self.cooling_down():
            return {"error": self.lang["msg"]["cooldown"].format(s=self.cooling_down())}
        q, issued = self.pending.pop(qid)
        correct = choice == q.answer
        delta, message = evaluate(self.rules, q, correct, time.time() - issued, earned_today, remaining)
        self.wrong_streak = 0 if correct else self.wrong_streak + 1
        if self.wrong_streak >= self.WRONG_LIMIT:
            self.wrong_streak = 0
            self.cooldown_until = time.time() + self.COOLDOWN
            message += self.lang["msg"]["streak"]
        return {"correct": correct, "answer": q.answer, "explanation": q.explanation, "more": q.more,
                "delta": delta, "message": message, "cooldown": self.cooling_down()}
