"""共通仕様（addr.py / privacy.py）の単体テスト。

仕様書4節・5節に書かれた例をそのまま通す。
処理を走らせる前に、いちばん最初にこれを実行する。落ちたら取りに行かない。
"""

import sys
import math
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import addr
import privacy


class TestAddr(unittest.TestCase):
    """仕様書4節のテスト。期待値は仕様書に書かれた値をそのまま写す。

    「キーが両方出ること」のような曖昧な書き方にしない。
    それだと丁目を落とすか含むかの食い違いが見つからなかった。
    """

    def test_umeda(self):
        r = addr.normalize("大阪府", "大阪市北区", "梅田一丁目１番１号")
        self.assertEqual(r["addr"], "大阪市北区梅田1-1-1")
        self.assertEqual(r["town"], "梅田1")
        self.assertEqual(r["addr_key"], "27127|梅田1-1-1")
        self.assertEqual(r["addr_key_town"], "27127|梅田1")

    def test_shioe(self):
        r = addr.normalize("兵庫県", "尼崎市", "潮江1丁目3番1号")
        self.assertEqual(r["addr_key"], "28202|潮江1-3-1")
        self.assertEqual(r["addr_key_town"], "28202|潮江1")

    def test_uegahara(self):
        """「番町」の番を置き換えない。町丁目は②の一覧で決まる。"""
        r = addr.normalize("兵庫県", "西宮市", "大字上ケ原　二番町3-5")
        self.assertEqual(r["addr_key"], "28204|上ケ原2番町3-5")
        self.assertEqual(r["addr_key_town"], "28204|上ケ原2番町")
        self.assertEqual(r["town_source"], "list")

    def test_town_rule_stages(self):
        """townの決め方は3段階（共通仕様4節）。"""
        # ① 置き換えた位置の手前
        self.assertEqual(
            addr.normalize("兵庫県", "尼崎市", "潮江1丁目3番1号")["town_source"], "marker")
        # ② 置き換えが起きなければ一覧で最長一致
        self.assertEqual(
            addr.normalize("兵庫県", "尼崎市", "北大物町")["town_source"], "list")

    def test_unresolved_town_is_empty(self):
        """①でも②でも決まらなければ空。推測で埋めない。

        途中で切った「上ケ原2番町3」のようなものを入れると、
        別の場所の記録が混ざる。空なら「つながらなかった」と分かるだけで済む。
        """
        r = addr.normalize("兵庫県", "見知らぬ市", "知らない町3-5", city_code="28999")
        self.assertEqual(r["town_source"], "unknown")
        self.assertEqual(r["addr_key_town"], "")
        self.assertTrue(r["addr_key"])   # 番地までのキーは出る

    def test_chome_only(self):
        """犯罪統計マップの入力。町丁目までしか無い。"""
        r = addr.normalize("兵庫県", "尼崎市", "潮江一丁目")
        self.assertEqual(r["addr_key"], "28202|潮江1")
        self.assertEqual(r["addr_key_town"], "28202|潮江1")

    def test_chome_is_kept_distinct(self):
        """丁目でまとめない。1丁目と5丁目が同じキーになってはいけない。"""
        a = addr.normalize("兵庫県", "尼崎市", "潮江一丁目")["addr_key_town"]
        b = addr.normalize("兵庫県", "尼崎市", "潮江五丁目")["addr_key_town"]
        self.assertEqual(b, "28202|潮江5")
        self.assertNotEqual(a, b)

    def test_town_without_number(self):
        """数字を持たない町名はそのまま。"""
        r = addr.normalize("兵庫県", "尼崎市", "北大物町")
        self.assertEqual(r["addr_key_town"], "28202|北大物町")

    def test_place_name_with_kanji_number(self):
        """地名の一部の漢数字は算用数字になるが、ハイフンにはしない。"""
        r = addr.normalize("兵庫県", "西宮市", "甲子園八番町")
        self.assertEqual(r["addr_key_town"], "28204|甲子園8番町")

    def test_city_code5(self):
        self.assertEqual(addr.city_code5("282022"), "28202")
        self.assertEqual(addr.city_code5("282049"), "28204")
        self.assertEqual(addr.city_code5(""), "")


class TestPrivacy(unittest.TestCase):
    def test_suppress_rate_population(self):
        self.assertTrue(privacy.suppress_rate(10, 499))
        self.assertFalse(privacy.suppress_rate(10, 500))

    def test_suppress_rate_zero_is_never_suppressed(self):
        """**0件は、人口がいくら小さくても伏せない。**

        率を伏せるのは「率 × 人口」で件数が戻るから。0件には戻る先が無い。
        伏せると、0件が率の地図で灰色に落ちて「0件はいちばん薄い階級の色」と
        食い違う（共通仕様3.2）。

        2026-09-19：docstring には「0件は伏せない」と書いてあったのに、
        人口を先に見ていたので 0件×小人口 が伏せられていた。
        **検査がその組み合わせを一度も当てていなかった。**
        実装と検査が同じ向きにずれると、通ったまま何年でも残る。
        """
        for pop in (1, 100, 499, 500, 5000):
            self.assertFalse(privacy.suppress_rate(0, pop), f"人口{pop}")

    def test_zero_population_has_no_rate(self):
        """人口0は、伏せる以前に率が定義できない（0では割れない）。

        2026-09-19：0件を先に見る直しを入れたら、人口0の町丁目47件で
        ゼロ除算になった。**直しが新しい不具合を作った。**
        順番は3段（人口0 → 0件 → 小人口・1〜2件）。
        """
        self.assertTrue(privacy.suppress_rate(0, 0))
        self.assertIsNone(privacy.rate_per_1k(0, 0))
        self.assertIsNone(privacy.rate_for(0, 0))

    def test_suppress_rate_count(self):
        self.assertTrue(privacy.suppress_rate(2, 5000))
        self.assertFalse(privacy.suppress_rate(3, 5000))

    def test_bucket_count(self):
        self.assertEqual(privacy.bucket_count(0), "0")
        self.assertEqual(privacy.bucket_count(1), "1-2")
        self.assertEqual(privacy.bucket_count(2), "1-2")
        self.assertEqual(privacy.bucket_count(3), "3")

    def test_rate_per_1k(self):
        self.assertIsNone(privacy.rate_per_1k(2, 5000))
        self.assertIsNone(privacy.rate_per_1k(10, 400))
        self.assertEqual(privacy.rate_per_1k(10, 5000), 2.0)

    def test_is_corp(self):
        self.assertTrue(privacy.is_corp("イオンモール株式会社"))
        self.assertFalse(privacy.is_corp("山田太郎"))

    def test_screen_and_index_differ(self):
        """画面表示と index.json の値は分ける（共通仕様3.1）。混ぜない。"""
        self.assertEqual(privacy.redact_name("山田太郎"), "個人")
        self.assertEqual(privacy.party_for_index("山田太郎"), "")
        self.assertEqual(privacy.redact_name("イオンモール株式会社"), "イオンモール株式会社")
        self.assertEqual(privacy.party_for_index("イオンモール株式会社"), "イオンモール株式会社")

    def test_party_kind(self):
        """4つを分ける（共通仕様3.1・5節）。"""
        self.assertEqual(privacy.party_kind("イオンモール株式会社"), "corp")
        self.assertEqual(privacy.party_kind("山田太郎"), "individual")
        self.assertEqual(privacy.party_kind(""), "individual")
        # 一次情報の側が名前を載せていない
        self.assertEqual(privacy.party_kind("", disclosed=False), "undisclosed")

    def test_redact_addr_by_kind(self):
        """町丁目まで丸めるのは individual のときだけ。"""
        a = "尼崎市潮江1-3-1"
        self.assertEqual(privacy.redact_addr(a, "individual"), "尼崎市潮江1")
        self.assertEqual(privacy.redact_addr(a, "corp"), a)
        self.assertEqual(privacy.redact_addr(a, "undisclosed"), a)
        self.assertEqual(privacy.redact_addr(a, "none"), a)



class TestComplementSuppress(unittest.TestCase):
    """補完的伏せ（共通仕様3.2）。

    まとまりの親が分かると、伏せた升の合計が引き算で出る。
    伏せた升がどれも1件か2件なら、2件の数 m がそこから確定する。
    m が 0 か k に張り付くか、k が1なら、升が特定できる。
    そのときは1〜2件ではない升を追加で伏せて、合計に3以上の未知数を混ぜる。
    """

    def solved(self, values):
        """読者の立場で解いて、1〜2件の升が決まってしまうかを見る。"""
        states = privacy.complement_suppress(values)
        total = sum(v for v, st in zip(values, states) if st != privacy.SHOWN)
        return privacy._pin_check(states, values, total)

    def test_k_is_one(self):
        """伏せた升が1つだけなら、そのまま出すと確定する。"""
        values = [9, 7, 1, 5]
        states = privacy.complement_suppress(values)
        self.assertIn(privacy.WITHHELD, states)
        self.assertFalse(self.solved(values))

    def test_all_ones(self):
        """伏せた升が全部1件（m=0）なら、全部特定できる。"""
        values = [9, 7, 1, 1, 1]
        self.assertIn(privacy.WITHHELD, privacy.complement_suppress(values))
        self.assertFalse(self.solved(values))

    def test_all_twos(self):
        """伏せた升が全部2件（m=k）でも同じ。"""
        values = [9, 7, 2, 2]
        self.assertIn(privacy.WITHHELD, privacy.complement_suppress(values))
        self.assertFalse(self.solved(values))

    def test_mixed_is_already_safe(self):
        """1件と2件が混ざっていれば、追加で伏せる必要はない。"""
        values = [9, 7, 1, 2, 1, 2]
        states = privacy.complement_suppress(values)
        self.assertNotIn(privacy.WITHHELD, states)
        self.assertFalse(self.solved(values))

    def test_nothing_to_hide(self):
        """1〜2件の升が無ければ、何も伏せない。"""
        self.assertEqual(privacy.complement_suppress([9, 7, 5]),
                         [privacy.SHOWN] * 3)

    def test_labels_are_merged_per_group(self):
        """補完的伏せを使ったまとまりでは、伏せた升の札をそろえる。

        「1-2」と「非公開」を並べると、どれが3件以上かが読者に分かり、
        1〜2件かもしれない升の数（隠れる場所）が減る。
        見た目の違いは、それ自体が数字になる（共通仕様3.2）。
        """
        states = privacy.complement_suppress([9, 7, 1, 5])
        self.assertNotIn(privacy.SMALL, states)          # 1-2 の札は残さない
        self.assertEqual(states.count(privacy.WITHHELD), 2)

    def test_labels_stay_split_when_not_needed(self):
        """補完的伏せを使っていないまとまりは、今までどおり「1-2」。"""
        states = privacy.complement_suppress([9, 7, 1, 2, 1, 2])
        self.assertNotIn(privacy.WITHHELD, states)
        self.assertEqual(states.count(privacy.SMALL), 4)

    def test_withheld_is_never_labelled_1_2(self):
        """補完的伏せは3件以上。「1-2」と書くと嘘になる。"""
        self.assertEqual(privacy.label_for_state(privacy.WITHHELD, None), "非公開")
        self.assertEqual(privacy.label_for_state(privacy.SMALL, None), "1-2件")
        self.assertEqual(privacy.label_for_state(privacy.SHOWN, 0), "0件")


class TestIsCorpBeyondWordList(unittest.TestCase):
    """語の一覧だけでは足りないもの（共通仕様3.1・5節）。

    2026-09-19：正本 #52 に合わせたとき、こちらに2つ無かった。
    カタカナ・ローマ字と、国と地方公共団体。呼んでいないから気づけなかった
    （このサイトは当事者を持たないので party_kind は常に "none"）。
    **共有モジュールは、使っていなくてもそろえる。** 次に写した人が古いものを持つ。
    """

    def test_katakana_or_romaji_is_corp(self):
        """戸籍の氏名はこの形にならない。"""
        for n in ("オークワ　ほか", "F.O.B COOP", "コープさっぽろ"):
            self.assertTrue(privacy.is_corp(n), n)

    def test_government_is_corp(self):
        """法人格の語を持たないが個人ではない。"""
        for n in ("大阪市", "兵庫県", "大阪市交通局", "○○町教育委員会", "国"):
            self.assertTrue(privacy.is_corp(n), n)

    def test_person_stays_person(self):
        for n in ("山田太郎", "カタカナ商事"):
            self.assertFalse(privacy.is_corp(n), n)

    def test_not_a_name_stays_person_side(self):
        """名前でない文言は「個人」側に置く。置き換えは呼び出し側の仕事。"""
        for n in ("未定", "（未定）", "―", "物品販売業を営む店舗"):
            self.assertFalse(privacy.is_corp(n), n)

    def test_shifted_address_is_not_government(self):
        """列がずれて住所が入ったものを自治体と取り違えない（伏せる側に倒す）。

        `…町` で終わるので、素朴に末尾だけ見ると自治体名と衝突する。
        """
        for n in ("大阪市北区角田町３番25号", "大阪市北区角田町"):
            self.assertFalse(privacy.is_corp(n), n)

    def test_truncated_corp_name_is_person_side(self):
        """法人名が途中で切れたものも伏せる側。置き換えや削除はしない。"""
        for n in ("三井住友ファイナンス", "大和ハウスリアルティ"):
            self.assertFalse(privacy.is_corp(n), n)


class TestPartyKindDisclosed(unittest.TestCase):
    """undisclosed にしてよいのは、名前の欄が無いと確かめたときだけ。"""

    def test_default_is_individual_not_undisclosed(self):
        self.assertEqual(privacy.party_kind("山田太郎"), "individual")

    def test_disclosed_false_is_undisclosed(self):
        self.assertEqual(privacy.party_kind("", disclosed=False), "undisclosed")

    def test_corp_is_corp(self):
        self.assertEqual(privacy.party_kind("株式会社テスト"), "corp")


class TestOnlyOneWayIsRejected(unittest.TestCase):
    """当てる組合せが1通りのまとまりは落とす（共通仕様3.2）。

    2026-09-19：失敗条件を `k < 2 or m <= 0 or m >= k` から
    `C(k, m) == 1` に書き換えた。**同値であることは確かめたが、
    テストが無かった。** 書き換えの根拠が手元の確認だけだと、
    次に誰かが片方を動かしたときに黙って別の規則になる。

    ここで両方を突き合わせる。どちらを直しても、ずれたら落ちる。
    """

    def test_old_and_new_conditions_agree(self):
        """遠回りな書き方と、正本の言葉が、同じものを指していること。"""
        for k in range(1, 40):
            for m in range(0, k + 1):
                old = (k < 2 or m <= 0 or m >= k)
                new = (math.comb(k, m) == 1)
                self.assertEqual(old, new, f"k={k} m={m}")

    def test_one_way_means_every_cell_is_determined(self):
        """組合せが1通りなら、伏せた升はすべて決まる。"""
        for k, m in ((1, 0), (1, 1), (5, 0), (5, 5), (106, 0), (106, 106)):
            self.assertEqual(math.comb(k, m), 1, f"k={k} m={m}")

    def test_margin_keeps_more_than_one_way(self):
        """余裕があるまとまりは、1通りに決まらないこと。"""
        for k, m in ((22, 11), (36, 19), (38, 17), (106, 53)):
            self.assertGreater(math.comb(k, m), 1, f"k={k} m={m}")


class TestSuppressCount(unittest.TestCase):
    """件数の線が母数を見ているか（2026-09-20）。

    **向きをまちがえないこと。** 「人口が小さいほど伏せる」を素直に当てると、
    もっとも特定に結びつきにくい升（住んでいない町の大きな件数）を消す。
    結びつくのは「人口が小さい**かつ**件数も小さい」升。

    **捕まえないもの。** 閾値が妥当か。書いてあるとおりかしか見ていない。
    """

    def test_zero_is_never_hidden(self):
        """0件は伏せない。伏せると「無い」が読めなくなる。"""
        for pop in (0, 4, 99, 100, 10000):
            self.assertFalse(privacy.suppress_count(0, pop), f"人口{pop}")

    def test_one_and_two_always_hidden(self):
        for pop in (0, 4, 99, 100, 10000, None):
            for n in (1, 2):
                self.assertTrue(privacy.suppress_count(n, pop), f"人口{pop} 件数{n}")

    def test_no_residents_is_not_hidden(self):
        """住んでいる人がいない町丁目。被害者は住民ではない。"""
        self.assertFalse(privacy.suppress_count(98, 0))
        self.assertFalse(privacy.suppress_count(3, 0))

    def test_more_than_residents_is_not_hidden(self):
        """件数が人口を超えている。被害者が住民でない証拠。"""
        self.assertFalse(privacy.suppress_count(28, 4))
        self.assertFalse(privacy.suppress_count(225, 93))

    def test_small_population_small_count_is_hidden(self):
        self.assertTrue(privacy.suppress_count(3, 26))
        self.assertTrue(privacy.suppress_count(5, 57))

    def test_big_population_small_count_is_not_hidden(self):
        """300人の町の3件は誰も特定しない。率の線（500）を流用しない。"""
        self.assertFalse(privacy.suppress_count(3, 300))
        self.assertFalse(privacy.suppress_count(3, privacy.MIN_POPULATION - 1))

    def test_two_lines_are_different_numbers(self):
        """守っているものが違うので、同じ数字にしない。"""
        self.assertNotEqual(privacy.TOKUTEI_FLOOR, privacy.MIN_POPULATION)


class TestFudaSoroe41(unittest.TestCase):
    """#41：同じまとまりに「1-2」と「非公開」を並べない。人口の線も同じ判定に入れる。

    数字はすべて作り物。人口 900 は線の外、26 は線の中（TOKUTEI_FLOOR 未満）。

    **捕まえないもの。** 札を分けることで漏れる範囲のうち、この関数の外で
    足した伏せ方（ここを通らない経路）。build.city_masu が全部ここを通すことは
    TestCityMasuGoesThroughOnePlace が見る。
    """

    P = privacy

    def labels(self, values, pops):
        st = self.P.complement_suppress(values, pops)
        return [v if x == self.P.SHOWN else ("1-2" if x == self.P.SMALL else "非公開")
                for v, x in zip(values, st)]

    def hidden_labels(self, values, pops):
        return {x for x in self.labels(values, pops) if isinstance(x, str)}

    # 1. 混在していた形 → 札がそろい、「非公開」を 3〜5件と読めない
    def test_mixed_group_is_unified(self):
        v, pop = [1, 2, 4, 30, 40, 50], [900, 900, 26, 900, 900, 900]
        self.assertEqual(self.labels(v, pop), ["非公開", "非公開", "非公開", 30, 40, 50])
        self.assertEqual(self.hidden_labels(v, pop), {"非公開"},
                         "「1-2」と「非公開」が同じまとまりに並んでいる")

    def test_reader_cannot_tell_which_is_small_population(self):
        """そろえた後は、伏せた升の範囲が札からは分からない（1〜2 と 3〜5 が同じ札）。"""
        a = self.labels([1, 2, 4, 30, 40, 50], [900, 900, 26, 900, 900, 900])
        b = self.labels([2, 1, 5, 30, 40, 50], [900, 900, 26, 900, 900, 900])
        self.assertEqual(a, b, "伏せた升の中身が違っても、画面は同じでなければならない")

    # 2. 補完的伏せが要る形 → 足される
    def test_single_small_population_cell_gets_a_partner(self):
        """人口の線の升が1つだけ（k=1）なら、親から引けばその升が出る。1つ足す。"""
        v, pop = [4, 30, 40, 50], [26, 900, 900, 900]
        got = self.labels(v, pop)
        self.assertEqual(sum(1 for x in got if x == "非公開"), 2)
        self.assertEqual(got[1], "非公開", "足すのは残りのうち一番小さい升")
        self.assertEqual(got[2:], [40, 50])

    def test_old_order_would_leak(self):
        """前の順番（伏せた後で人口の線を足す）なら、この形は親から引いて戻った。"""
        v = [4, 30, 40, 50]
        old = self.P.complement_suppress(v)                  # 人口を渡さない＝前の形
        self.assertEqual(old, [self.P.SHOWN] * 4, "前は補完的伏せが走らなかった")
        # 前は build 側でこの1升だけ伏せた → 親 124 − 見せた 120 = 4 で戻る
        self.assertEqual(sum(v) - sum(v[1:]), v[0])

    # 3. 補完的伏せが要らない形 → 余計に隠さない
    def test_no_extra_cells_when_not_needed(self):
        v, pop = [1, 2, 4, 30, 40, 50], [900, 900, 26, 900, 900, 900]
        shown = [x for x in self.labels(v, pop) if not isinstance(x, str)]
        self.assertEqual(shown, [30, 40, 50], "見せてよい升まで隠した")

    def test_group_without_small_population_keeps_1_2(self):
        """人口の線の升が無いまとまりは「1-2」のまま。余計にそろえない。"""
        v, pop = [1, 2, 30, 40, 50], [900] * 5
        self.assertEqual(self.labels(v, pop), ["1-2", "1-2", 30, 40, 50])

    def test_without_pops_behaves_as_before(self):
        """人口を渡さない呼び方（市の升など）は、前とまったく同じ。"""
        for v in ([1, 2, 30, 40, 50], [9, 7, 5], [9, 7, 1, 5], [1, 1, 30]):
            with self.subTest(v=v):
                self.assertEqual(self.P.complement_suppress(v),
                                 self.P.complement_suppress(v, None))

    # 4. 閾値の前後
    def test_thresholds(self):
        base = [30, 40, 50]
        F, M = self.P.TOKUTEI_FLOOR, self.P.TOKUTEI_MAX
        cases = [
            ((3, F - 1), True,  "人口が線の1つ下・3件"),
            ((3, F),     False, "人口がちょうど線・3件"),
            ((M, 26),    True,  "件数がちょうど上限"),
            ((M + 1, 26), False, "件数が上限の1つ上"),
            ((3, 0),     False, "人口0（住民がいない）"),
            ((28, 4),    False, "件数が人口を超える"),
            ((2, 900),   True,  "2件は人口によらず伏せる"),
            ((0, 26),    False, "0件は伏せない"),
        ]
        for (n, pop), hidden, why in cases:
            with self.subTest(why=why):
                got = self.labels([n] + base, [pop] + [900] * 3)[0]
                self.assertEqual(isinstance(got, str), hidden, why)


class TestCityMasuGoesThroughOnePlace(unittest.TestCase):
    """build.city_masu は、伏せる判断を privacy.complement_suppress の1か所に任せる。

    前は complement_suppress のあとで人口の線を足していて、札そろえから外れた（#41）。
    **捕まえないもの。** city_masu 以外の経路。件数を書き出す経路はほかに作らない決まり。
    """

    def test_city_masu_output_is_unified(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import build
        vals = [[1, 0], [2, 0], [4, 0], [30, 0], [40, 0], [50, 0]]   # 作り物
        pops = [900, 900, 26, 900, 900, 900]
        n, w, _ = build.city_masu(vals, pops)
        col = [(n[i][0], w[i][0]) for i in range(len(vals))]
        hidden = [ww for nn, ww in col if nn is None]
        self.assertEqual(len(hidden), 3)
        self.assertTrue(all(hidden), "伏せた升の札がそろっていない")
        self.assertEqual([nn for nn, _ in col if nn is not None], [30, 40, 50])

    def test_no_suppression_after_the_one_place(self):
        """complement_suppress のあとで、升を伏せ直していないこと（ソースを読む）。"""
        src = (Path(__file__).resolve().parent.parent / "scripts" / "build.py").read_text(encoding="utf-8")
        body = src[src.index("def city_masu"):src.index("def add_neighbors")]
        after = body[body.index("complement_suppress("):]
        self.assertNotIn("suppress_count", after, "伏せる判断が2か所に分かれている")


if __name__ == "__main__":
    unittest.main(verbosity=2)
