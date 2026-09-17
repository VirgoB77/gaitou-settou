"""知らないものを黙って捨てない（共通仕様9節）。

県警CSVは年によって様式が変わる。見たことのない列や値に出会ったら、
値は `_extra` に残したうえで、ここに記録して data/parse-unknown.md に書き出す。

黙って捨てると、手口が1つ増えたことに気づかないまま、
何年も欠けた地図を出し続けることになる。
突合できなかった行を unmatched.csv に残しているのと同じ考え方を、列にも当てる。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

_REGISTRY = config.ROOT / "data" / "known_columns.json"

_found = defaultdict(set)   # (種類, どこで) -> {中身, ...}


def load_registry():
    if not _REGISTRY.exists():
        return {"common": [], "extra": {}}
    return json.loads(_REGISTRY.read_text(encoding="utf-8"))


_REG = load_registry()


def known_columns(teguchi):
    """必ずあるはずの列。"""
    return list(_REG.get("common", [])) + list(_REG.get("extra", {}).get(teguchi, []))


def optional_columns():
    """年によって有る無いが変わる列。無くても「消えた」と report しない。

    兵庫県警は2020年に様式を変えている。
        2018・2019年  発生場所の属性（1列）
        2020年以降    発生場所 ＋ 発生場所の詳細（2列）
    """
    return list(_REG.get("optional", []))


def note(kind, where, item):
    """見たことのないものを1件記録する。"""
    _found[(kind, where)].add(str(item))


def check_columns(columns, teguchi, where):
    """CSVの見出しを既知の一覧と突き合わせる。知らない列も、消えた列も記録する。"""
    known = known_columns(teguchi)
    if not known:
        return []
    allowed = set(known) | set(optional_columns())
    unknown = [c for c in columns if c not in allowed]
    missing = [c for c in known if c not in columns]
    for c in unknown:
        note("知らない列", where, c)
    for c in missing:
        note("あるはずの列が無い", where, c)
    return unknown


def check_value(column, value, allowed, where):
    """決まった値しか来ないはずの列に、知らない値が来ていないか。"""
    v = (value or "").strip()
    if v and v not in allowed:
        note(f"知らない値（{column}）", where, v)


def has_findings():
    return bool(_found)


def write_report(path=None):
    """data/parse-unknown.md に書き出す。何も無ければ、無いと書く。"""
    path = Path(path) if path else config.ROOT / "data" / "parse-unknown.md"
    lines = [
        "# 解析中に出会った、知らないもの",
        "",
        "共通仕様9節。県警CSVの様式が変わったときに気づくための記録。",
        "このファイルは実行のたびに作り直される。",
        "",
        f"既知の列の一覧：`data/known_columns.json`（{_REG.get('_confirmed', '')}）",
        "",
    ]
    # 「確認してください」は、本当に知らないものだけに出す。
    # 毎回出るものを混ぜると、本当の変化を見落とす。
    need = {k: v for k, v in _found.items() if not k[0].startswith("参考")}
    info = {k: v for k, v in _found.items() if k[0].startswith("参考")}

    def block(items):
        out = []
        for (kind, where), vals in sorted(items.items()):
            out.append(f"### {kind}")
            out.append("")
            out.append(f"- 場所：`{where}`")
            for it in sorted(vals):
                out.append(f"- `{it}`")
            out.append("")
        return out

    if not need:
        lines += ["## 今回の実行", "", "確認が要るものはありませんでした。", ""]
    else:
        lines += ["## 今回の実行", ""]
        lines += ["**確認してください。** 様式が変わった可能性があります。", ""]
        lines += block(need)
        lines += [
            "### 直し方",
            "",
            "1. 配布元のページで様式が変わっていないか確かめる",
            "2. 取り込むべき列なら `scripts/config.py` と `data/known_columns.json` に足す",
            "3. 取り込まなくてよい列なら `data/known_columns.json` に足すだけでよい",
            "",
        ]
    if info:
        lines += ["## 参考（毎回出るもの。対応は要りません）", ""]
        lines += block(info)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(need)
