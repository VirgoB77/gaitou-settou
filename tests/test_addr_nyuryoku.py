"""addr.py に入る値の境界を見張る（2026-09-26・統括判断、production 再開前の残件B）。

このサイトの addr.normalize() に入るのは、**e-Stat 境界データの町丁目の名前だけ**。
番地の付いた住所は入らない。だから addr.py の ①②は、正本 4節と少し意味が違っても
いまは実害が無い。

    ① 「丁目」だけでなく、数字のあとの「番地」「番」「号」でも切る
    ② 町名一覧で当たったあとが数字かを確かめない

ところが**番地付きの住所が来ると、addr.py は黙って決めてしまう。**
「…3番地」は①で丁目のように決まり、「X3-5-1」は②で丁目の無い「X」に決まりうる。
どちらも空にならず、「決められない」の記録にも出ない。
実際に、区画の名前に「3番地」を混ぜた出力で、それまでの検査は1本も鳴らなかった。

**町名をうまく推し量るためのものではない。** 前提（町丁目の名前だけが入る）が崩れたら、
公開の前に止めるためのもの。workflow では「作る」のあと、「もう一度検査する
（作ったものに対して）」で走り、落ちたら「結果をしまう」に進まない。

見るのは data/build/*.geojson（build.py が書き、公開側にも入っている。git が追跡している）。
"""

import ast
import json
import re
import subprocess
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "scripts"))
import addr  # noqa: E402
import config  # noqa: E402

# 数字のあとの「番地」「番」「号」。**「番」の字そのものは禁じない。**
# 「7番町」「7番丁」の番は町名の一部で、数字のあとでない番（地名の中の番）もある
_BANCHI = re.compile(r"[0-9]+(番地|番(?![町丁])|号)")
# 数字に付いたハイフン（「3-5」「3-5-1」の形）。ハイフンに見える字をまとめて見る
_HYPHEN = re.compile(r"[0-9][-‐‑‒–—―−－ー]|[-‐‑‒–—―−－][0-9]")
# ①の印として許すのは、末尾の「N丁目」だけ
_CHOME_END = re.compile(r"[0-9]+丁目$")


def shitagoshirae(name):
    """addr.normalize() が町丁目を切り出す前にする下ごしらえと、同じ手順。

    ここが addr.py とずれたら、下の「一覧から決まった町名＝名前全体」が
    849 件まとめて落ちるので、黙ってはずれない。
    """
    s = unicodedata.normalize("NFKC", name or "").strip()
    s = re.sub(r"[\s　]+", "", s)
    s = s.replace("大字", "").replace("字", "")
    return addr._kanji_digits(s)


def kukaku():
    """data/build/*.geojson を全部拾う（市の名前を並べない）。"""
    return sorted(config.BUILD.glob("*.geojson"))


class 区画の名前は町丁目名だけ(unittest.TestCase):

    def test_市ごとの出力がそろっている(self):
        # 0件で「全部通った」にしない。市を足したのに出力が無ければ、ここで止まる
        codes = {p.stem for p in kukaku()}
        for c in config.CITIES:
            self.assertIn(c["code"], codes, f"data/build/{c['code']}.geojson が無い")

    def test_名前が住所の形をしていない(self):
        towns = json.loads((ROOT / "common" / "town_list.json").read_text(encoding="utf-8"))["towns"]
        mita, dame = 0, []
        for p in kukaku():
            fc = json.loads(p.read_text(encoding="utf-8"))
            P = fc["properties"]
            code = P["city_code"]
            ichiran = set(towns.get(code, ()))
            for f in fc["features"]:
                q = f["properties"]
                mita += 1
                s = shitagoshirae(q["name"])
                r = addr.normalize(P["pref"], P["city"], q["name"], city_code=code)
                basho = f"{p.name} 区画 {q['code']}"
                if _BANCHI.search(s):
                    dame.append(f"{basho}：数字のあとに番地・番・号がある")
                if _HYPHEN.search(s):
                    dame.append(f"{basho}：数字にハイフンが付いている")
                if s not in ichiran:
                    dame.append(f"{basho}：名前が町名一覧（common/town_list.json）に無い")
                if r["town_source"] == "unknown":
                    dame.append(f"{basho}：町丁目が決まらない（unknown）")
                elif r["town_source"] == "marker":
                    if not (_CHOME_END.search(s) and s == r["town"] + "丁目"):
                        dame.append(f"{basho}：①の印が末尾の「N丁目」ではない")
                elif r["town"] != s:
                    dame.append(f"{basho}：町名一覧で名前の途中までしか当たっていない")
                if (q["addr_key"], q["addr_key_town"]) != (r["addr_key"], r["addr_key_town"]):
                    dame.append(f"{basho}：保存された鍵が、いまの addr.py で作り直した値と違う")
        self.assertGreater(mita, 0, "区画を1つも見ていない")
        self.assertEqual(dame, [], f"{len(dame)} 件。町丁目の名前だけ、という前提が崩れた"
                         "（推し量って公開せず、ここで止める）")


def yobidashi(tree):
    """addr.normalize を指している所の数。呼び出しと、呼ばずに渡すのも数える。

    import の形を読んで、addr モジュールに付けた名前を覚えてから数える。
    コメント・文字列・unicodedata.normalize・scripts/normalize.py の normalize は数えない。
    """
    mod, fn = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in ("addr", "common.addr"):
                    mod.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.level == 0:
            for a in n.names:
                if n.module in ("addr", "common.addr") and a.name == "normalize":
                    fn.add(a.asname or a.name)
                elif n.module == "common" and a.name == "addr":
                    mod.add(a.asname or a.name)

    def namae(v):
        if isinstance(v, ast.Name):
            return v.id
        if isinstance(v, ast.Attribute):
            m = namae(v.value)
            return m and f"{m}.{v.attr}"
        return None

    kazu = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == "normalize" and namae(n.value) in mod:
            kazu += 1
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in fn:
            kazu += 1
    return kazu


class 本番の呼び出し元はbuildだけ(unittest.TestCase):
    """区画の名前を渡す scripts/build.py の1か所だけ。

    増えたら、その入力が町丁目の名前だけかを確かめないまま公開へ進みうる。
    **増やすときは、ここを直す前に、入力の前提を確かめる。**
    tests/ は仕様の例（番地付きを含む）を直接入れるので、数えない。
    """

    def test_呼び出し元(self):
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard", "--", "*.py"],
                           capture_output=True, check=True)
        files = [f for f in r.stdout.decode("utf-8").split("\0") if f]
        self.assertGreater(len(files), 0, "調べる .py が1つも無い")
        found, yomenai = {}, []
        for f in files:
            if f.split("/")[0] == "tests":
                continue
            try:
                tree = ast.parse((ROOT / f).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, FileNotFoundError) as e:
                yomenai.append(f"{f}: {e.__class__.__name__}")
                continue
            k = yobidashi(tree)
            if k:
                found[f] = k
        self.assertEqual(yomenai, [], "読めない .py がある（呼び出し元を確かめられない）")
        self.assertEqual(found, {"scripts/build.py": 1})


class 公開の前に走る(unittest.TestCase):
    """この検査が、作った直後・公開の前に走ること。

    段の順番（作る → もう一度検査する → 結果をしまう）は test_workflow.py が見る。
    ここでは、その段から tests/ までがつながっていることを見る。
    """

    def test_もう一度検査する段がtestsを走らせる(self):
        wf = (ROOT / ".github" / "workflows" / "koushin.yml").read_text(encoding="utf-8")
        m = re.search(r"- name: もう一度検査する（作ったものに対して）\n(.*?)(?=\n\s*- name:|\Z)", wf, re.S)
        self.assertIsNotNone(m, "「もう一度検査する（作ったものに対して）」の段が無い")
        # 段の中のコメントにも check_all.sh の名前が出てくる。コメントでは走らないので外して見る
        gyou = [l for l in m.group(1).splitlines() if not l.strip().startswith("#")]
        self.assertTrue(any(re.search(r"(^|\s)sh scripts/check_all\.sh(\s|$)", l) for l in gyou),
                        "「もう一度検査する」の段が scripts/check_all.sh を走らせていない")
        sh = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
        self.assertRegex(sh, r"(?m)^checks=\"-m\|unittest\|discover\|-s\|tests$")
        self.assertTrue(Path(__file__).name.startswith("test"), "unittest discover が拾わない名前")


if __name__ == "__main__":
    unittest.main()
