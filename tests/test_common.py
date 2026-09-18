"""共通仕様（addr.py / privacy.py）の単体テスト。

仕様書4節・5節に書かれた例をそのまま通す。
処理を走らせる前に、いちばん最初にこれを実行する。落ちたら取りに行かない。
"""

import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
