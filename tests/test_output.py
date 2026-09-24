"""出力そのものを見張る（共通仕様3.2・5節）。

privacy.py を迂回した値が1件でもあれば落ちる。
書き出したあとに走らせる。ここが落ちたら公開しない。
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_small_counts as leak
import config
import privacy

# 画面と record が使う「非公開」の札。geojson の properties から取る
WITHHELD_LABEL = "非公開"

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
        """count が null なら札は2種類のどちらか。数なら同じ数の文字列。

        **札が2種類ある**（共通仕様6節）。

          "1-2"   … 値は 1 か 2
          "非公開" … 値は 1 以上。**3 以上かもしれない**

        前は "1-2" しか認めていなかった。母数の側の線を入れて、
        3件以上でも伏せる升ができたので直した（2026-09-20）。
        **「1-2」と書くと嘘になる升**があるのが理由で、
        検査の歯を抜いたのではない。

        **捕まえないもの。** どちらの札が正しいか。
        値を持っていないので、札と値の食い違いは見られない。
        見ているのは「知らない札が出ていないか」だけ。
        """
        fuseji = {f"1-{privacy.BUCKET_MAX}", WITHHELD_LABEL}
        for m in self.masu:
            if m["count"] is None:
                self.assertIn(m["count_label"], fuseji, m)
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


class TestTextMatchesData(unittest.TestCase):
    """**文面が約束していることを、データが満たしているか。**

    注記とポリシーは「自動販売機ねらい・ひったくりは町丁目には出さず、
    市ごと・年ごとの件数を出している」と書いている。
    書いてあるのに無い、は共通仕様3.5に反する（伏せ方の問題ではなく、
    嘘になるという問題）。逆に、町丁目に出ていたら伏せ方の問題になる。

    2026-09-17：姉妹セッションの検証が「書いてあるが実物に無い」と報告してきた。
    実際には index.json にあったが、**画面には1件も出ていなかった**ので
    文面のほうを正確にした。ここはデータ側の裏づけを見張る。
    """

    def setUp(self):
        self.d = load()

    def test_city_only_teguchi_are_in_counts(self):
        """町丁目に出していない手口が、市の升には出ていること。"""
        kinds = {m["kind"].split("/", 1)[1] for m in self.d["counts_by_city"]}
        for t in config.CITY_ONLY:
            self.assertIn(t, kinds,
                          f"{t} を市の升に出していない。注記が約束している")

    def test_city_only_teguchi_are_not_in_town_records(self):
        """逆に、町丁目の個票には出ていないこと（束ねたら細かいほうは出さない）。"""
        layer_names = {l["name"] for l in config.TOWN_LAYERS}
        for r in self.d["records"]:
            self.assertIn(r["kind"].split("/", 1)[1], layer_names, r["kind"])

    def test_city_masu_cover_every_year(self):
        """市の升は手口 × 年をすべて埋めていること。欠けると和が変わる。"""
        years = {m["period"] for m in self.d["counts_by_city"]}
        cities = {m["city_code"] for m in self.d["counts_by_city"]}
        want = len(years) * len(cities) * len(config.TEGUCHI)
        self.assertEqual(len(self.d["counts_by_city"]), want,
                         "市の升に抜けがある")


class TestGrainMismatch(unittest.TestCase):
    """**町丁目と市で、粒度をわざとずらしておく。**

    町丁目の個票は8年合計だけ。市の升は年ごと。
    だから市の升を年で引いても、相手になる「町丁目 × 年」が存在しない。
    引けるのは8年合計どうしだけになる。

    2026-09-17：姉妹セッションが「意図した設計ではないかもしれないが
    実際に効いている。直すときに壊さないでほしい」と指摘してきた。
    効いているなら、意図に格上げして検査で留める。
    偶然効いているものは、次の変更で黙って消える。
    """

    def setUp(self):
        self.d = load()

    def test_town_records_have_one_period_only(self):
        """町丁目の個票の期間は1種類だけ。年を足したら、ここが落ちる。"""
        periods = {r["period"] for r in self.d["records"]}
        self.assertEqual(len(periods), 1,
                         f"町丁目に複数の期間が出ている: {sorted(periods)}")

    def test_town_period_is_the_whole_window(self):
        """その1種類が、窓ぜんぶ（年ごとではない）であること。"""
        period = next(iter({r["period"] for r in self.d["records"]}))
        self.assertIn("-", period, f"町丁目の期間が単年になっている: {period}")

    def test_city_grain_is_finer_than_town(self):
        """市の升のほうが細かいこと。同じ粒度になると直接引ける。"""
        town = {r["period"] for r in self.d["records"]}
        city = {m["period"] for m in self.d["counts_by_city"]}
        self.assertFalse(town & city,
                         "町丁目と市が同じ粒度の升を持っている。引き算の相手になる")


class TestNotCounted(unittest.TestCase):
    """数えなかったものの出し方（共通仕様6節 `not_counted`）。

    **4つとも出す。0でも出す。** 欠けている鍵は0ではない。
    3つしか出さないサイトがあると、横断で読む側は「0」と
    「このサイトは数えていない」を見分けられない。

    **そして、市・層・手口・年のどれにも割らない。**
    割ると `true(市,層) − unmatched(市,層) = 出している升の合計` が成り立ち、
    伏せた升の親になる（`docs/突合率.md` が行数そのものを書かないのと同じ理由）。
    理由ごとの全体合計だけなら、どの粒度にも割り当てられない。

    **捕まえないもの。**
      ・箱の割り当てが正しいか。合計しか見ていない。
        「町丁目が空欄」を undecided に入れても、ここは通る
      ・`check_small_counts` はここを見ていない。`text_parents` は
        `.json` を読まない。**だから粒度の番はここが持つ**
    """

    HAKO = ("unresolved", "unobserved", "undecided", "gone")

    def setUp(self):
        self.nc = load().get("not_counted")

    def test_all_four_keys_present(self):
        self.assertEqual(sorted(self.nc or {}), sorted(self.HAKO),
                         "\n  4つとも出すこと。0でも出す。欠けている鍵は0ではない")

    def test_flat_not_split_by_any_grain(self):
        """入れ子にしない。割った瞬間に伏せた升の親になる。"""
        fukai = {k: v for k, v in (self.nc or {}).items()
                 if not isinstance(v, int) or isinstance(v, bool)}
        self.assertEqual(fukai, {},
                         "\n  not_counted を市・層・手口・年に割らない。"
                         "割ると true − unmatched が出している升の合計になり、"
                         "伏せた升の親になる")

    def test_sum_matches_unmatched_rows(self):
        """合計が、突合できなかった行数と合うこと。行を落としていないか。"""
        import csv
        un = config.BUILD / "unmatched.csv"
        if not un.exists():
            raise unittest.SkipTest(
                "unmatched.csv が無い（公開用の木。金庫にだけ置く）。合計は照合していない")
        n = sum(1 for _ in csv.DictReader(un.open(encoding="utf-8-sig")))
        self.assertEqual(sum(self.nc.values()), n,
                         f"\n  箱の合計と unmatched.csv の行数が違う。"
                         f"どこかの行が箱に入っていない（行数 {n:,}）")


class TestCountsAgree(unittest.TestCase):
    """数が互いに合っているか（共通仕様9節）。

    **走らせる前に、何が出たら異常かを決めておく。**
    決めていないと、画面に出ていても読み飛ばす。
    850ファイルの `M` は目の前にあったが、「コードしか触っていないのに」と
    思わなければ素通りしていた（2026-09-19）。

    期待値は**互いから導く。** 固定の数を書くと、市を足すたびに古びて、
    古びた検査は直され方が「期待値のほうを書き換える」になる。

    **3本は区画数を共有している。** だから `index.json` の `areas` が
    1つ狂うと**3本同時に鳴る。** 原因は3つではなく1つ。
    鳴った本数を、見つかった不具合の数として読まないこと（共通仕様9節
    「鳴った理由が、鳴らしたかった理由と同じか」）。

    **捕まえないもの。**
      ・中身。数が合っていても、中身が入れ替わっていれば通る
      ・区画の数そのものが減ったとき。両方が同時に減れば釣り合う
      ・`areas` 自身。ここでは期待値の出どころなので、真偽は問えない
    """

    def setUp(self):
        self.d = load()
        bi = json.loads((config.BUILD / "index.json").read_text(encoding="utf-8"))
        self.areas = sum(c["areas"] for c in bi["cities"])

    def _msg(self, nani, doko, shiki, mita, hazu):
        """鳴ったとき、**どちらが真か**と**出どころ**を必ず書く。

        既定の文面は `1698 != 1700` で、どちらが実測でどちらが期待値か
        書いていない。3本同時に鳴くときは、なおさら読めない。
        """
        return (f"\n  {nani}：実測 {mita:,}（{doko}）"
                f"\n  期待 {hazu:,}＝{shiki}"
                f"\n  期待値の出どころは index.json の areas={self.areas:,}。"
                f"ここが狂うと、ほかの数の検査も一緒に鳴く")

    def test_records_is_areas_times_layers(self):
        want = self.areas * len(config.TOWN_LAYERS)
        got = len(self.d["records"])
        self.assertEqual(got, want, self._msg(
            "records の数", "index.json",
            f"区画 {self.areas:,} × 層 {len(config.TOWN_LAYERS)}", got, want))

    def test_cho_pages_is_areas_plus_list(self):
        pages = list((ROOT / "cho").glob("*.html"))
        if not pages:
            raise unittest.SkipTest("cho/ がまだ無い")
        want = self.areas + 1
        self.assertEqual(len(pages), want, self._msg(
            "cho/*.html の枚数", "cho/ を数えた",
            f"区画 {self.areas:,} + 一覧ページ 1", len(pages), want))

    def test_sitemap_is_areas_plus_three(self):
        sm = ROOT / "sitemap.xml"
        if not sm.exists():
            raise unittest.SkipTest("sitemap.xml がまだ無い")
        locs = re.findall(r"<loc>", sm.read_text(encoding="utf-8"))
        want = self.areas + 3
        self.assertEqual(len(locs), want, self._msg(
            "sitemap の <loc> の数", "sitemap.xml",
            f"区画 {self.areas:,} + トップ・policy・一覧 3", len(locs), want))

    def test_every_record_points_at_a_real_page(self):
        """url が実在するページを指していること。リンク切れを数で見ない。"""
        if not any((ROOT / "cho").glob("*.html")):
            raise unittest.SkipTest("cho/ がまだ無い")
        missing = [r["url"] for r in self.d["records"]
                   if not (ROOT / "cho" / (r["url"].rsplit("/", 1)[1])).exists()]
        self.assertEqual(missing[:3], [])


class TestNoMixedLabels(unittest.TestCase):
    """#41：公開しているデータで、同じまとまりに「1-2」と「非公開」が並んでいないか。

    まとまり＝市 × 層（親 S＝市の升を層のぶん足したもの。引き算の単位）。
    混ざると、補完的伏せが発動していないことが読め、「非公開」が人口の線の升
    （3〜TOKUTEI_MAX 件）だと分かる。

    **捕まえないもの。** 札がそろっていても、伏せた升が引き算で戻るかどうか。
    それは check_small_counts が見る。
    """

    def groups(self):
        for f in sorted(config.BUILD.glob("*.geojson")):
            fc = json.loads(f.read_text(encoding="utf-8"))
            for i, layer in enumerate(fc["properties"]["layers"]):
                cells = [ft["properties"] for ft in fc["features"]]
                yield (fc["properties"]["city"], layer["name"],
                       sum(1 for p in cells if p["n"][i] is None and not p["w"][i]),
                       sum(1 for p in cells if p["n"][i] is None and p["w"][i]))

    def test_geojson_groups_are_not_mixed(self):
        mixed = [(c, l, a, b) for c, l, a, b in self.groups() if a and b]
        self.assertEqual(mixed, [], "\n  同じまとまりに「1-2」と「非公開」が並んでいる"
                                    "（市, 層, 1-2 の升, 非公開 の升）")

    def test_unified_group_has_at_least_two(self):
        one = [(c, l) for c, l, a, b in self.groups() if b == 1 and not a]
        self.assertEqual(one, [], "\n  伏せた升が1つだけ。親から引けば戻る")

    def test_records_are_not_mixed(self):
        d = load()
        by = {}
        for r in d["records"]:
            if r["count"] is None:
                _, city, _, layer = r["id"].split(":")
                by.setdefault((city, layer), set()).add(r["count_label"])
        mixed = {k: v for k, v in by.items() if len(v) > 1}
        self.assertEqual(mixed, {}, "\n  index.json の record で札が混ざっている")


if __name__ == "__main__":
    unittest.main(verbosity=2)
