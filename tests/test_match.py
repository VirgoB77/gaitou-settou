#!/usr/bin/env python3
"""突合の分かれ道を、実際に走らせる。

**この置き場では `build.py` が走らない**（GeoPandas / pyshp が無い）。
だから `police.match` の新しい経路も、走らせずに公開していた。
「10/2 の月次で動くはず」は、**効いている理由を書いていない期待**。

`boundary.Boundary` は標準ライブラリだけで作れる辞書の入れ物なので、
偽の区画を渡せば分かれ道は全部通せる。境界ファイルは要らない。

見るものは2つ。

  1. **分かれ道が、期待どおりの箱を返すか**（実際に走らせる）
  2. **失敗の出口が全部 `hazure` を通っているか**（ソースを読む）

2つめが要る。生の `return None, "理由", key` を1つ足すと、
`build.py` の `reason.hako` が `AttributeError` で落ちる。
**落ちるのは月次の本番**で、ここでは何も起きない。

**捕まえないもの。**

  ・箱の割り当てが妥当か。書いてあるとおりかしか見ていない
  ・本物の境界データ。偽の区画で分かれ道だけを通している
  ・`build.py` そのもの。走らせられないままで、ここは `police` までしか見ない
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import boundary
import config
import police
from normalize import normalize

C = config.COLS


def row(cho):
    return {C["cho"]: cho, C["city"]: "尼崎市", C["teguchi"]: "自転車盗",
            C["hour"]: "10", C["date"]: "20240101"}


def bd(*names):
    """偽の境界。名前だけ持っていればよい。

    **鍵は正規化後の名前。** 本物も正規化して積んでいる。
    ここで生の名前を積むと、`武庫之荘西` が `武庫ノ荘西` にならず、
    寄せられるはずのものが寄せられない。
    一度それで落として、**対象ではなく道具のほうを疑うのに時間を使った。**
    偽データは、本物と同じ作り方にする。
    """
    return boundary.Boundary(
        {normalize(n): {"key": normalize(n), "name": n} for n in names}, 0, {})


class TestWakaremichi(unittest.TestCase):
    """分かれ道を1つずつ通す。"""

    def hako(self, cho, areas):
        area, reason, _ = police.match(row(cho), bd(*areas))
        self.assertIsNone(area, f"「{cho}」が突合できてしまった")
        self.assertIn(reason.hako, police.HAKO)
        return reason.hako

    def test_sonomama_icchi(self):
        area, reason, _ = police.match(row("北大物町"), bd("北大物町"))
        self.assertIsNotNone(area)
        self.assertEqual(reason, "そのまま一致")

    def test_oya_ni_yoseru(self):
        """丁目に分かれていない親には寄せてよい。"""
        area, reason, _ = police.match(row("武庫之荘西2丁目"), bd("武庫之荘西"))
        self.assertIsNotNone(area, "親に寄せられていない")
        self.assertIn("寄せた", reason)

    def test_kara_wa_unobserved(self):
        """語が無い。取りに行けば手に入る。"""
        self.assertEqual(self.hako("", ["北大物町"]), "unobserved")
        self.assertEqual(self.hako("   ", ["北大物町"]), "unobserved")

    def test_chome_nashi_wa_undecided(self):
        """語は読めるが、元データに丁目が無い。正本が決めないと減らない。"""
        self.assertEqual(self.hako("潮江", ["潮江1丁目", "潮江2丁目"]), "undecided")

    def test_oya_to_chome_heizon_wa_undecided(self):
        """親と丁目が併存。寄せると推測になる。"""
        self.assertEqual(
            self.hako("名神町3丁目", ["名神町", "名神町1丁目", "名神町2丁目"]),
            "undecided")

    def test_ban_ga_furui_wa_unobserved(self):
        """その版に無いだけ。新しい年次を取れば手に入る（2026-09-19・統括の判断）。

        「無い」ではなく「その版には無い」。**下敷きにするデータにも版がある。**
        """
        self.assertEqual(self.hako("柏木町5丁目", ["北大物町"]), "unobserved")


class TestDeguchi(unittest.TestCase):
    """失敗の出口が全部 `hazure` を通っているか。**ソースを読む。**

    走らせるだけでは足りない。書いていない分かれ道は通らない。
    """

    def test_every_failure_goes_through_hazure(self):
        src = (ROOT / "scripts" / "police.py").read_text(encoding="utf-8")
        m = re.search(r"\ndef match\(.*?(?=\ndef )", src, re.S)
        self.assertIsNotNone(m, "police.match が見つからない")
        nama = [l.strip() for l in m.group(0).splitlines()
                if "return None," in l and "hazure(" not in l]
        self.assertEqual(nama, [],
                         "\n  失敗の出口が hazure を通っていない。"
                         "箱が付かないので build.py が本番で AttributeError になる")


if __name__ == "__main__":
    unittest.main(verbosity=2)
