"""Python と JS が、同じ入力に同じ答えを返すか（共通仕様9節）。

同じ判断を2か所に書いて、2か所とも間違えた（2026-09-19・率の順番）。
**「同じように書いてある」は、一致していることの証拠にならない。**
生成で1か所にできないなら、両方に同じ入力を当てて答えを突き合わせる。

node が無い環境では飛ばす。**飛ばしたことを黙らない**（skip として出る）。
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
import privacy

# 条件が2つなら組は4つ。**端（0件・人口0）を必ず入れる。**
# 不具合は、たいてい端と端が重なるところにある。
COUNTS = [None, 0, 1, 2, 3, 9, 100]
POPS = [0, 1, 100, 499, 500, 5000]


class TestPythonJsAgree(unittest.TestCase):
    def setUp(self):
        if not shutil.which("node"):
            self.skipTest("node が無いので突き合わせを飛ばす")

    def test_rate_per_1k_agrees(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        m = re.search(r"function ratePer1k\(v, pop\) \{.*?\n\}", html, re.S)
        self.assertIsNotNone(m, "JS の ratePer1k が見つからない")
        js = m.group(0).replace("fc.properties.min_population",
                                str(privacy.MIN_POPULATION))
        js = js.replace("fc.properties.max_suppress_count",
                        str(privacy.MAX_SUPPRESS_COUNT))
        cases = [[c, p] for c in COUNTS for p in POPS]
        prog = (js + "\nconst out = " + json.dumps(cases)
                + ".map(([v, p]) => ratePer1k(v, p));"
                + "\nconsole.log(JSON.stringify(out));")
        r = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        want = [privacy.rate_for(c, p) for c, p in cases]
        for (c, p), g, w in zip(cases, got, want):
            self.assertEqual(g, w, f"count={c} population={p}：JS={g} Python={w}")


class TestRateReasonGrid(unittest.TestCase):
    """条件が2つなら組は4つ。**4つ全部を当てる。**

    人口の条件を件数10で、件数の条件を人口5000で当てていて、
    「0件かつ小人口」を一度も当てていなかった。不具合はそこにしか無かった。
    """

    def test_all_four_corners(self):
        table = {
            (0, 0): privacy.NO_POPULATION,      # 端と端。ここが抜けていた
            (0, 400): privacy.SHOWN_RATE,       # 0件・小人口 ← 不具合はここ
            (0, 5000): privacy.SHOWN_RATE,
            (9, 0): privacy.NO_POPULATION,
            (9, 400): privacy.SUPPRESSED_RATE,  # 小人口
            (9, 5000): privacy.SHOWN_RATE,
            (1, 5000): privacy.SUPPRESSED_RATE,  # 1〜2件
            (2, 400): privacy.SUPPRESSED_RATE,   # 両方
        }
        for (c, p), want in table.items():
            self.assertEqual(privacy.rate_reason(c, p), want, f"count={c} pop={p}")

    def test_zero_count_is_shown_at_every_population(self):
        for p in POPS:
            want = privacy.NO_POPULATION if p == 0 else privacy.SHOWN_RATE
            self.assertEqual(privacy.rate_reason(0, p), want, f"pop={p}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
