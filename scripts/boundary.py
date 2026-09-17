"""e-Stat 境界データ（Shapefile）の読み込み。

町丁目を「サイトで1つの区画として見せる単位」にまとめる。
2つの処理が要る。

1. 同名ポリゴンの統合
   e-Stat は同じ町丁目名を複数レコードに分けていることがある。
   例）西立花町二丁目 = 28202102102(人口487) + 28202102202(人口1674)
   利用者から見れば同じ「西立花町2丁目」なので、人口を足し、形を束ねる。
   分けたまま名前で突合すると、片方にだけ件数が乗り、人口も半分になる。

2. 丁目を持つ親の判定
   県警CSVにしかない丁目（武庫之荘西2丁目など）を親に寄せてよいかの判断材料。
   判定は match.py 側で行う。ここでは材料だけ用意する。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normalize import normalize

HCODE_CHOMOKU = 8101      # 町丁目。8154(水面調査区)などは対象外
_CHOME_SUFFIX = re.compile(r"(\d+)丁目$")


def parent_name(norm):
    """正規化済みの名前から、丁目を落とした親の名前を返す。丁目が無ければ None。"""
    m = _CHOME_SUFFIX.search(norm)
    return norm[: m.start()] if m else None


class Boundary:
    def __init__(self, areas, skipped, merged):
        self.areas = areas        # 正規化名 -> dict
        self.skipped = skipped    # HCODEで除外した件数
        self.merged = merged      # 統合が起きた正規化名 -> [KEY_CODE, ...]

    def get(self, norm):
        return self.areas.get(norm)

    def has_chome_children(self, parent):
        """その親名で「〜N丁目」が e-Stat 側に存在するか。"""
        return any(parent_name(k) == parent for k in self.areas)

    def __len__(self):
        return len(self.areas)


def load(shp_path):
    """Shapefile を読み、同名をまとめた Boundary を返す。"""
    import shapefile      # 読むときだけ要る。升の作り方はこれに依らない

    sf = shapefile.Reader(str(shp_path), encoding="cp932")
    areas, merged, skipped = {}, {}, 0

    for i, rec in enumerate(sf.records()):
        d = rec.as_dict()
        if d["HCODE"] != HCODE_CHOMOKU:
            skipped += 1
            continue
        key = normalize(d["S_NAME"])
        a = areas.get(key)
        if a is None:
            areas[key] = {
                "key": key,
                "name": d["S_NAME"],
                "key_codes": [d["KEY_CODE"]],
                "jinko": int(d["JINKO"]),
                "setai": int(d["SETAI"]),
                "shape_indexes": [i],
            }
        else:
            a["key_codes"].append(d["KEY_CODE"])
            a["jinko"] += int(d["JINKO"])
            a["setai"] += int(d["SETAI"])
            a["shape_indexes"].append(i)
            merged[key] = a["key_codes"]

    return Boundary(areas, skipped, merged)
