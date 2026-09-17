"""県警CSVの読み込みと、町丁目区画への突合。

突合の判断は match() の1か所に集める。
どの行がどう扱われたかを、必ず理由つきで返す。
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import boundary
import config
import unknown
from normalize import city_code5, normalize

C = config.COLS


def detect_encoding(path):
    """CSVの文字コードを見分ける。

    兵庫県警の配布は年によって違う。
        2024年 … UTF-8（先頭にBOM）
        2025年 … Shift_JIS
    固定で決め打つと、年を足したときに読めなくなる。
    """
    head = path.read_bytes()[:4096]
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp932"


def load(city_code, year):
    """その市・その年の行を返す。手口ファイルはすべて読む。

    見たことのない列や値に出会ったら、捨てずに記録する（共通仕様9節）。
    知らない列の値は行の `_extra` に残す。
    """
    pref = config.PREFS[config.city(city_code)["pref"]]
    rows = []
    for teguchi in config.TEGUCHI:
        path = config.RAW / pref.csv_name(year, teguchi)
        if not path.exists():
            continue
        with open(path, encoding=detect_encoding(path), newline="") as f:
            reader = csv.DictReader(f)
            unknown_cols = unknown.check_columns(reader.fieldnames or [], teguchi, path.name)
            for r in reader:
                if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
                    # 全列が空の行。配布ファイルの末尾に付いていることがある。
                    # 飛ばすが、黙って捨てずに1件として記録する。
                    unknown.note("参考：全列が空の行（配布ファイルの末尾）", path.name, "（空行）")
                    continue
                if city_code5(r.get(C["code"], "")) != city_code:
                    continue
                # 罪名・手口は決まった値しか来ないはず。増えていたら気づけるようにする。
                unknown.check_value(C["teguchi"], r.get(C["teguchi"]), config.TEGUCHI, path.name)
                unknown.check_value("罪名", r.get("罪名"), config.ZAIMEI, path.name)
                if unknown_cols:
                    r["_extra"] = {c: r.get(c) for c in unknown_cols}
                r["_file"] = path.name
                r["_year"] = year
                rows.append(r)
    return rows


def match(row, bd):
    """1行を町丁目区画に突合する。(区画 or None, 理由, 正規化後の名前)。"""
    name = row[C["cho"]]
    if not name.strip():
        return None, "町丁目が空欄", ""

    key = normalize(name)
    area = bd.get(key)
    if area:
        return area, "そのまま一致", key

    parent = boundary.parent_name(key)
    if parent is None:
        # 県警側が丁目を省いている（潮江、南塚口町 など）。
        # どの丁目か決められないので寄せない。
        return None, "丁目の記載がなく、どの区画か決められない", key

    if bd.get(parent):
        if bd.has_chome_children(parent):
            # 例）名神町3丁目。e-Stat に 名神町1丁目・2丁目 が別にあるため、
            # 丁目なしの「名神町」に寄せると推測になる。
            return None, f"e-Statに「{parent}」と丁目が併存し、寄せると推測になる", key
        # 例）武庫之荘西2丁目。e-Stat は「武庫之荘西」を丁目に分けていない。
        # 範囲は同じなので、親に寄せてよい。
        return bd.get(parent), f"e-Statが丁目に分けていないため「{parent}」に寄せた", key

    return None, "e-Statに該当する町丁目がない", key


def load_boundary(city_code):
    c = config.city(city_code)
    return boundary.load(config.RAW / f"境界_{c['name']}" / f"r2ka{city_code}")


def hour_band(v):
    v = (v or "").strip()
    if not v.isdigit():
        return None
    return config.HOUR_BANDS[min(int(v) // 6, 3)]
