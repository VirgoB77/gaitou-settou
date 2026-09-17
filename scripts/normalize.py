"""町丁目名の正規化。

県警CSVと e-Stat 境界データは、同じ町丁目を別の表記で書いている。

    県警CSV      潮江１丁目        全角の算用数字
    e-Stat境界   潮江一丁目        漢数字

このモジュールは両方を同じ「突合キー」に落とす。変換ルールを増やすときは
必ずここに書き、他のスクリプトに正規化処理を散らさないこと。
"""

import re
import unicodedata

# 丁目に使われる漢数字（尼崎市は十二丁目まで実在する）
_KANJI_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _kanji_to_int(s):
    """一〜九十九の漢数字を整数にする。読めなければ None。"""
    if s in _KANJI_DIGIT:
        return _KANJI_DIGIT[s]
    if "十" not in s:
        return None
    upper, _, lower = s.partition("十")
    tens = _KANJI_DIGIT.get(upper, 1) if upper else 1
    ones = _KANJI_DIGIT.get(lower, None) if lower else 0
    if ones is None:
        return None
    return tens * 10 + ones


# 表記揺れの置き換え。左を右に寄せる。
_CHAR_FIXES = [
    ("ヶ", "ケ"),   # 弥生ヶ丘町 / 弥生ケ丘町
    ("ヵ", "ケ"),
    ("之", "ノ"),   # 玄番北之町 / 玄番北ノ町
    ("の", "ノ"),
    ("ッ", "ツ"),
    ("　", ""),     # 全角スペース
    (" ", ""),
]

_CHOME_KANJI = re.compile(r"([〇一二三四五六七八九十]+)丁目$")
_CHOME_ARABIC = re.compile(r"(\d+)丁目$")


def normalize(name):
    """町丁目名を突合キーに変換する。変換できない場合も文字列を返す。"""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKC", name).strip()  # 全角英数→半角もここで済む

    for a, b in _CHAR_FIXES:
        s = s.replace(a, b)

    # 「一丁目」→「1丁目」。地名の一部の漢数字（甲子園八番町など）は
    # 末尾の「〜丁目」に限定しているので巻き込まない。
    m = _CHOME_KANJI.search(s)
    if m:
        n = _kanji_to_int(m.group(1))
        if n is not None:
            s = s[: m.start()] + f"{n}丁目"

    # 「01丁目」のような余分な0を落とす
    m = _CHOME_ARABIC.search(s)
    if m:
        s = s[: m.start()] + f"{int(m.group(1))}丁目"

    return s


def city_code5(code):
    """県警CSVの6桁市区町村コード（チェックデジット付き）を e-Stat の5桁にする。

        282022 → 28202  (尼崎市)
        282049 → 28204  (西宮市)
    """
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return c[:5]
    if len(c) == 5 and c.isdigit():
        return c
    return ""


if __name__ == "__main__":
    cases = [
        ("潮江１丁目", "潮江1丁目"),
        ("大物町一丁目", "大物町1丁目"),
        ("東園田町十二丁目", "東園田町12丁目"),
        ("甲子園八番町", "甲子園8番町"),   # NFKCで数字は変わらない＝漢数字は温存される
        ("弥生ヶ丘町", "弥生ケ丘町"),
        ("玄番北之町", "玄番北ノ町"),
    ]
    for src, _ in cases:
        print(f"  {src!r:20} -> {normalize(src)!r}")
