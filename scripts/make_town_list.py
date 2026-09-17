"""町丁目の一覧を作る（共通仕様4節の②で使う）。

出どころは e-Stat 令和2年国勢調査 小地域（町丁・字等別）境界データ。
このサイトが人口と境界に使っているものと同じなので、
一覧と地図が食い違うことがない。国交省の位置参照情報を別に持つより確実。

出力は common/town_list.json。addr.py が読む。
"""

import json
import sys
import unicodedata
from pathlib import Path

import shapefile

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import config
from addr import _kanji_digits

sys.stdout.reconfigure(encoding="utf-8")

HCODE_CHOMOKU = 8101
OUT = config.ROOT / "common" / "town_list.json"


def normalized_name(s):
    """addr.normalize と同じ下ごしらえをした形で持つ。突き合わせるため。"""
    s = unicodedata.normalize("NFKC", s or "").strip()
    s = "".join(s.split())
    s = s.replace("大字", "").replace("字", "")
    return _kanji_digits(s)


def main():
    towns = {}
    for c in config.CITIES:
        path = config.RAW / f"境界_{c['name']}" / f"r2ka{c['code']}"
        sf = shapefile.Reader(str(path), encoding="cp932")
        names = set()
        for rec in sf.records():
            d = rec.as_dict()
            if d["HCODE"] != HCODE_CHOMOKU:
                continue
            n = normalized_name(d["S_NAME"])
            if n:
                names.add(n)
        towns[c["code"]] = sorted(names)
        print(f"  {c['name']}（{c['code']}）  {len(names)} 件")

    OUT.write_text(json.dumps({
        "_note": "町丁目の一覧。共通仕様4節②の最長一致に使う。addr.py が読む。",
        "_source": "e-Stat 令和2年国勢調査 小地域（町丁・字等別）境界データ",
        "_normalized": "NFKC・空白除去・大字/字除去・漢数字→算用数字 を済ませた形",
        "towns": towns,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT.relative_to(config.ROOT)}  {OUT.stat().st_size:,} バイト")


if __name__ == "__main__":
    main()
