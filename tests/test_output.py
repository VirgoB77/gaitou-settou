"""出力そのものを見張る（共通仕様3.2・5節）。

privacy.py を迂回した値が1件でもあれば落ちる。
書き出したあとに走らせる。ここが落ちたら公開しない。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_small_counts as leak
import config
import privacy

INDEX = ROOT / "index.json"


def load():
    if not INDEX.exists():
        raise unittest.SkipTest("index.json がまだ無い（先に build.py を走らせる）")
    return json.loads(INDEX.read_text(encoding="utf-8"))


class TestSmallCountsAreHidden(unittest.TestCase):
    """1〜2件の升が、実数のまま出ていないこと。"""

    def setUp(self):
        self.d = load()
        self.masu = self.d["records"] + self.d["counts_by_city"]

    def test_count_and_label_both_present(self):
        for m in self.masu:
            self.assertIn("count", m, m)
            self.assertIn("count_label", m, m)

    def test_small_counts_are_null(self):
        """count は 1 か 2 にならない。なるなら伏せ忘れ。"""
        bad = [m for m in self.masu if m["count"] in (1, 2)]
        self.assertEqual(bad, [], f"1〜2件が実数のまま出ている: {bad[:3]}")

    def test_label_matches_count(self):
        """count が null なら label は "1-2"。数なら同じ数の文字列。"""
        for m in self.masu:
            if m["count"] is None:
                self.assertEqual(m["count_label"], f"1-{privacy.BUCKET_MAX}", m)
            else:
                self.assertEqual(m["count_label"], str(m["count"]), m)

    def test_rate_follows_privacy(self):
        """率を出している升が、伏せる条件に当てはまっていないこと。"""
        for m in self.masu:
            n = m["count"] if m["count"] is not None else privacy.BUCKET_MAX
            should = privacy.suppress_rate(n, m["population"])
            self.assertEqual(m["rate_suppressed"], should, m)
            if should:
                self.assertIsNone(m["rate_per_1k"], m)


class TestNoNames(unittest.TestCase):
    """当事者を持たないサイトなので、名前の欄は空で kind は none。"""

    def test_party_is_empty(self):
        d = load()
        for r in d["records"]:
            self.assertEqual(r["party"], "", r)
            self.assertEqual(r["party_kind"], "none", r)


class TestRequiredFields(unittest.TestCase):
    """出典と取得日は必須。空にしない（共通仕様3.5）。"""

    def test_source_and_fetched(self):
        d = load()
        for r in d["records"]:
            self.assertTrue(r["source_url"], r)
            self.assertTrue(r["fetched_on"], r)
            self.assertTrue(r["url"].startswith("http"), r)

    def test_masu_keys(self):
        """升は city_code × kind × period の3本を必ず持つ（共通仕様6節）。"""
        d = load()
        seen = set()
        for m in d["counts_by_city"]:
            key = (m["city_code"], m["kind"], m["period"])
            self.assertTrue(all(key), m)
            self.assertNotIn(key, seen, f"升が重複している: {key}")
            seen.add(key)


class TestNoRawValuesAnywhere(unittest.TestCase):
    """index.json 以外の公開物も見る（共通仕様5節）。

    升を1つずつ見るだけでは足りなかった。前は index.json しか走査しておらず、
    geojson が生の実数を、横棒が幅を、色の階級が凡例を通して、
    同じ数字を素通りで出していた。**目で読める形になっているものは全部、
    数字を出している。**
    """

    @classmethod
    def setUpClass(cls):
        for c in config.CITIES:
            if not (config.BUILD / f'{c["code"]}.geojson').exists():
                raise unittest.SkipTest("geojson がまだ無い（先に build.py を走らせる）")
        cls.fcs, cls.idx = leak.load()

    def test_geojson_has_no_small_counts(self):
        """geojson に 1〜2 が実数のまま入っていないこと。

        ブラウザが「1-2」と書いても、ファイルを開けば読めるなら伏せていない。
        """
        self.assertEqual(leak.raw_values(self.fcs, self.idx)[:5], [])

    def test_no_parent_totals(self):
        """親の合計を公開物に置かないこと（突合率の行数など）。"""
        self.assertEqual(leak.parent_totals(self.fcs), [])

    def test_city_rate_is_rounded_enough(self):
        """市平均の丸めが、伏せた子を守れる粗さであること。"""
        self.assertEqual(leak.city_rate_margin(self.fcs)[:5], [])

    def test_class_breaks_avoid_suppressed_range(self):
        """色の階級の切れ目が 1〜2 の中を通らないこと。

        通ると1件と2件が別の色に落ち、凡例が1か2かを答えてしまう。
        """
        self.assertEqual(leak.class_breaks(), [])

    def test_bars_have_no_width_when_suppressed(self):
        """伏せた行の横棒に幅が無いこと。幅は目で読める数字そのもの。"""
        if not any((ROOT / "cho").glob("*.html")):
            raise unittest.SkipTest("cho/ がまだ無い（先に pages.py を走らせる）")
        self.assertEqual(leak.bar_widths(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
