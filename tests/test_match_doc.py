#!/usr/bin/env python3
"""docs/突合率.md は生成物、scripts/突合率.template.md が人の書く正本（#43）。

前は build が文面ごと docs/突合率.md を書いていて、手で直した中身を毎回戻していた。
戻る中身には、公開側から外すと決めた文も入っていた。

見るものは3つ。

  1. **正本は build に消されない。** build は正本に書かない
  2. **数は毎回 build から入る。** 差し込みが消えたら止まる（黙って数が消えない）
  3. **commit されている生成物は、正本から作ったもの。** 生成物を手で直すと落ちる
     （直した文は次の build で消える。直すなら正本を直す）

**捕まえないもの。**
  ・割合の数そのものが正しいか。行数は公開していないので、ここでは作り直せない
  ・正本に書いた文の中身の良し悪し
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "common"), str(ROOT / "scripts")]

import build  # noqa: E402
import police  # noqa: E402

MARK = "この一文は人が書いた。build で消えてはいけない。"
FAKE = [  # 作り物
    {"city": "甲市", "rows": 2000, "unmatched": 10,
     "hako": {"unresolved": 1, "unobserved": 4, "undecided": 5, "gone": 0}},
    {"city": "乙市", "rows": 3000, "unmatched": 3,
     "hako": {"unresolved": 0, "unobserved": 2, "undecided": 1, "gone": 0}},
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.tpl, self.out = d / "tpl.md", d / "out.md"
        text = build.MATCH_DOC_TEMPLATE.read_text(encoding="utf-8")
        self.tpl.write_text(text.replace("# 突合率\n", f"# 突合率\n\n{MARK}\n", 1),
                            encoding="utf-8")

    def build_once(self):
        build.write_match_doc(FAKE, self.tpl, self.out)
        return self.out.read_text(encoding="utf-8")


class TestHandWrittenSurvives(Base):
    # 1. 人が書いた文は消えない
    def test_hand_written_text_survives_build(self):
        before = self.tpl.read_bytes()
        out = self.build_once()
        self.build_once()
        self.assertIn(MARK, out, "正本に書いた文が生成物に出ていない")
        self.assertEqual(self.tpl.read_bytes(), before, "build が正本を書き換えた")

    def test_template_notes_are_not_published(self):
        out = self.build_once()
        self.assertNotIn("<!--", out)
        self.assertNotIn("正本", out, "正本の注釈が生成物に漏れた")


class TestNumbersAreGenerated(Base):
    # 2. 数は正しい場所へ入る
    def test_numbers_go_to_the_output(self):
        out = self.build_once()
        self.assertIn("| 甲市 | 0.50% |", out)
        self.assertIn("| 乙市 | 0.10% |", out)
        self.assertIn("| **合計** | **0.26%** |", out)
        nc = build.not_counted_of(FAKE)
        for h in police.HAKO:
            self.assertRegex(out, rf"\| `{h}` \| {nc[h]} \|")
        self.assertNotRegex(out, r"\{\{", "埋まっていない差し込みが残った")

    def test_same_numbers_as_index_json(self):
        """docs と index.json は同じ関数で数える。"""
        self.assertEqual(build.not_counted_of(FAKE),
                         {"unresolved": 1, "unobserved": 6, "undecided": 6, "gone": 0})

    def test_removed_placeholder_stops_build(self):
        self.tpl.write_text(self.tpl.read_text(encoding="utf-8").replace("{{割合の表}}", ""),
                            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build_once()

    def test_unknown_placeholder_stops_build(self):
        self.tpl.write_text(self.tpl.read_text(encoding="utf-8") + "\n{{謎の数}}\n",
                            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.build_once()


class TestIdempotent(Base):
    # 3. 2回目に差が出ない
    def test_build_twice_is_idempotent(self):
        first = self.build_once()
        second = self.build_once()
        self.assertEqual(first, second)


class TestCommittedDoc(unittest.TestCase):
    """commit されている docs/突合率.md は、正本から作ったものか。"""

    def setUp(self):
        self.doc = build.MATCH_DOC.read_text(encoding="utf-8")
        self.nc = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))["not_counted"]

    def test_committed_doc_is_made_from_template(self):
        """生成物を手で直すと落ちる。直した文は次の build で消えるので、正本を直すこと。

        割合の表は、行数を公開していないので作り直せない。表だけは今の中身をそのまま
        使い、それ以外（文面と not_counted の数）が正本から作ったものと一致するかを見る。
        """
        table = [l for l in self.doc.splitlines()
                 if re.match(r"^\| (市 \| 突合|\*\*合計\*\* \||[^|`*]+ \| [0-9.]+% \|$)", l)
                 or l == "|---|---:|"]
        fake = [{"city": "x", "rows": 1, "unmatched": 0, "hako": self.nc}]
        body = build.render_match_doc(fake, build.MATCH_DOC_TEMPLATE.read_text(encoding="utf-8"))
        gen = "\n".join(["| 市 | 突合できなかった割合 |", "|---|---:|",
                         "| x | 0.00% |", "| **合計** | **0.00%** |"])
        expected = body.replace(gen, "\n".join(table))
        self.assertEqual(self.doc, expected,
                         "\n  docs/突合率.md が正本から作ったものと違う。手で直していないか。"
                         "\n  直すなら scripts/突合率.template.md を直す（生成物は次の build で消える）")

    def test_paths_in_doc_exist_in_this_repo(self):
        """生成物が、公開側に無いファイルを指していないか（前は unmatched.csv を指していた）。"""
        paths = re.findall(r"`((?:data|docs|scripts|cho)/[^`]+)`", self.doc)
        missing = [p for p in paths if not (ROOT / p).exists()]
        self.assertEqual(missing, [])

    def test_links_to_doc_still_resolve(self):
        from urllib.parse import unquote
        found = 0
        for page in ("policy.html", "index.html"):
            html = (ROOT / page).read_text(encoding="utf-8")
            for href in re.findall(r'href="([^"#?]+)"', html):
                href = unquote(href)
                if "突合率" not in href:
                    continue
                found += 1
                with self.subTest(page=page, href=href):
                    self.assertTrue((ROOT / href.lstrip("/")).exists(), f"{page} → {href}")
        # 1件も見つからずに通ると、見ていないのと同じ
        self.assertGreater(found, 0, "突合率へのリンクが1件も見つからない")


class TestMainEndToEnd(unittest.TestCase):
    """build.main() を丸ごと2回走らせる。入力の段（build_city）だけ偽物にする。

    write_match_doc を直接呼ぶ検査だけでは、main() が昔のように文面を自分で書く形に
    戻っても気づかない。**通常の build の入口から通す。**
    書き出し先は一時ディレクトリ。repo のファイルには書かない。外へも出ない。
    """

    def setUp(self):
        import datetime
        import socket
        import types
        from unittest import mock
        import config
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        (t / "data" / "build").mkdir(parents=True)
        (t / "docs").mkdir()
        idx = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))

        def fake_build_city(code):
            fc = json.loads((ROOT / "data" / "build" / f"{code}.geojson").read_text(encoding="utf-8"))
            m = FAKE[0]
            ty = {}
            for c in idx["counts_by_city"]:
                if c["city_code"] == code:
                    ty.setdefault(c["period"], {})[c["kind"].split("/", 1)[1]] = \
                        c["count"] if c["count"] is not None else 1
            return fc, dict(m, city=fc["properties"]["city"]), ty

        def no_net(*a, **k):
            raise AssertionError("外へ繋ごうとした")

        self.repo_doc = build.MATCH_DOC.read_bytes()
        self.repo_tpl = build.MATCH_DOC_TEMPLATE.read_bytes()
        self.doc = t / "docs" / "突合率.md"
        # 正本も写しを渡す。**本物の正本に書ける形で検査を走らせない。**
        # 壊して確かめたとき、本物の正本を空にしたことがある（2026-09-24）
        self.tpl = t / "scripts" / "突合率.template.md"
        self.tpl.parent.mkdir()
        # 写しには人が書いた一文を足しておく。それが2回の build を通って残るかを見る
        self.tpl_bytes = self.repo_tpl.decode("utf-8").replace(
            "# 突合率\n", f"# 突合率\n\n{MARK}\n", 1).encode("utf-8")
        self.assertNotEqual(self.tpl_bytes, self.repo_tpl, "一文を足せなかった")
        self.tpl.write_bytes(self.tpl_bytes)

        # 日付を止める。日をまたいで走ると、2回目の差が日付だけで出てしまう
        class Day(datetime.date):
            @classmethod
            def today(cls):
                return cls(2026, 1, 1)
        fake_dt = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime)
                                           if not k.startswith("_")})
        fake_dt.date = Day
        self.t = t
        for patch in (mock.patch.object(config, "ROOT", t),
                      mock.patch.object(config, "BUILD", t / "data" / "build"),
                      mock.patch.object(build, "MATCH_DOC", self.doc),
                      mock.patch.object(build, "MATCH_DOC_TEMPLATE", self.tpl),
                      mock.patch.object(build, "build_city", fake_build_city),
                      mock.patch.object(build, "dt", fake_dt),
                      mock.patch.object(socket, "create_connection", no_net),
                      mock.patch.object(socket.socket, "connect", no_net),
                      mock.patch("sys.stdout", new=__import__("io").StringIO())):
            patch.start()
            self.addCleanup(patch.stop)

    def snapshot(self):
        return {p.relative_to(self.t).as_posix(): p.read_bytes()
                for p in sorted(self.t.rglob("*")) if p.is_file()}

    def test_main_twice_keeps_hand_written_text_and_is_idempotent(self):
        build.main()
        first_all = self.snapshot()
        first = self.doc.read_text(encoding="utf-8")
        build.main()
        second_all = self.snapshot()
        self.assertIn(MARK, first, "人が正本に書いた一文が build で消えた")
        self.assertEqual(first, self.doc.read_text(encoding="utf-8"),
                         "build を2回走らせたら docs/突合率.md が変わった")
        # docs だけでなく、build が書いたもの全部が2回目で変わらない
        changed = sorted(k for k in first_all.keys() | second_all.keys()
                         if first_all.get(k) != second_all.get(k))
        self.assertEqual(changed, [], "2回目の build で差が出た")
        self.assertGreater(len(first_all), 3, "build が何も書いていない（見ていないのと同じ）")
        # 正本の文面が、通常の build の入口を通っても出ている
        body = re.sub(r"\A\s*<!--.*?-->\s*\n", "",
                      self.tpl_bytes.decode("utf-8"), flags=re.S)
        for line in body.splitlines():
            if line and "{{" not in line:
                self.assertIn(line, first, f"正本の文が build で消えた: {line}")
        # build は正本（写し）に書いていない。repo の正本と生成物にも触っていない
        self.assertEqual(self.tpl.read_bytes(), self.tpl_bytes, "build が正本に書いた")
        self.assertEqual((ROOT / "scripts" / "突合率.template.md").read_bytes(), self.repo_tpl)
        self.assertEqual((ROOT / "docs" / "突合率.md").read_bytes(), self.repo_doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
