# 正本は ogataten-nippo/docs/kyotsu-shiyo.md（4節）。
# https://github.com/VirgoB77/ogataten-nippo/blob/main/docs/kyotsu-shiyo.md
# このファイルにコピーを置く決まりだが、仕様そのものを直すときは正本を先に直す。
# Python標準ライブラリのみ。submodule も pip も使わない。
"""住所を4サイトで突き合わせるための正規化。

キーは2本出す。

    addr_key       番地まで      "27127|梅田1-1-1"
    addr_key_town  町丁目まで    "27127|梅田1"      ← 丁目を含む

丁目は必ず含める。「梅田」でまとめると、大阪市北区梅田1〜3丁目が1つに潰れ、
件数も人口も混ざって意味をなさない。犯罪統計マップは丁目ごとに数えている。
"""

import json
import re
import unicodedata
from pathlib import Path

_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_KANJI_RUN = re.compile(r"[〇一二三四五六七八九十]+")

# 丁目・番地・番・号 をハイフンにするのは、**直後が数字か文字列の終わりのときだけ**。
# 素朴に全部を置き換えると町名が壊れる。
#     上ケ原2番町3-5 → 上ケ原2-町3-5   ← 「番町」の番まで置き換えてしまう
_MARKER = re.compile(r"(丁目|番地|番|号)(?=\d|$)")

# 町丁目の切り出しに使う。丁目があれば、そこまでが町丁目。
_CODES_FILE = Path(__file__).with_name("city_codes.json")
_TOWNS_FILE = Path(__file__).with_name("town_list.json")


def _kanji_to_int(s):
    if s in _KANJI:
        return _KANJI[s]
    if "十" not in s:
        return None
    upper, _, lower = s.partition("十")
    tens = _KANJI.get(upper, 1) if upper else 1
    ones = _KANJI.get(lower, 0) if lower else 0
    if ones is None:
        return None
    return tens * 10 + ones


def _kanji_digits(s):
    """漢数字を算用数字にする（正規化ルール2）。

    「二番町」も「2番町」になる。町名が壊れるのを防ぐのはルール3の役目で、
    ここではない。
    """
    def rep(m):
        n = _kanji_to_int(m.group(0))
        return str(n) if n is not None else m.group(0)
    return _KANJI_RUN.sub(rep, s)


def _load_codes():
    if _CODES_FILE.exists():
        return json.loads(_CODES_FILE.read_text(encoding="utf-8"))
    return {}


_CITY_CODES = _load_codes()


def city_code_of(pref, city):
    """都道府県名＋市区町村名から5桁コードを引く。分からなければ空文字。"""
    return _CITY_CODES.get(f"{pref}{city}", "")


def city_code5(code):
    """6桁（チェックデジット付き）を5桁にする。282022 → 28202"""
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return c[:5]
    if len(c) == 5 and c.isdigit():
        return c
    return ""


def _load_towns():
    if _TOWNS_FILE.exists():
        return json.loads(_TOWNS_FILE.read_text(encoding="utf-8")).get("towns", {})
    return {}


_TOWNS = _load_towns()


def _split_town(s, code):
    """町丁目までの部分を切り出す。3段階（共通仕様4節）。

    「最初のハイフンより前」では決まらない。
    「町名のあとの数字」が丁目なのか番地なのか、文字列だけでは区別がつかない。

        梅田1-1-1      の最初の 1 = 丁目        → town は 梅田1
        上ケ原2番町3-5  の 3       = 番地の始まり → town は 上ケ原2番町

    戻り値は (町丁目, 決め方)。決まらなければ ("", "unknown")。
    """
    # ① 自分がハイフンに置き換えた場所があれば、その手前まで。
    #    入力にもとからあったハイフンと取り違えないため、置き換えた位置を見る。
    m = _MARKER.search(s)
    if m:
        return s[: m.start()], "marker"

    # ② 1つも置き換えなかったときは、町丁目の一覧で最長一致。
    cands = _TOWNS.get(code, ())
    best = ""
    for name in cands:
        if s.startswith(name) and len(name) > len(best):
            best = name
    if best:
        return best, "list"

    # ③ どちらでも決まらなければ空。推測で埋めない。
    #    黙って間違えた町丁目でつなぐと、別の場所の記録が混ざる。
    return "", "unknown"


def normalize(pref, city, addr, city_code=None):
    """住所を正規化して、突き合わせ用のキーを返す。"""
    pref = (pref or "").strip()
    city = (city or "").strip()
    code = city_code5(city_code) if city_code else city_code_of(pref, city)

    s = unicodedata.normalize("NFKC", addr or "").strip()   # 全角英数 → 半角
    s = re.sub(r"[\s　]+", "", s)                            # 空白を除去
    s = s.replace("大字", "").replace("字", "")              # 大字・字を除去
    s = _kanji_digits(s)                                     # 漢数字 → 算用数字

    town, how = _split_town(s, code)

    # 丁目・番地・番・号 → ハイフン（直後が数字か終わりのときだけ）
    body = _MARKER.sub("-", s)
    body = re.sub(r"[-−]+", "-", body).strip("-")

    return {
        "pref": pref,
        "city": city,
        "city_code": code,
        "town": town,
        "town_source": how,          # marker / list / unknown
        "addr": f"{city}{body}" if city else body,
        "addr_key": f"{code}|{body}" if code else "",
        "addr_key_town": f"{code}|{town}" if code and town else "",
    }
