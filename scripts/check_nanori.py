#!/usr/bin/env python3
"""日付の名乗りが外れていないかを見る（共通仕様3.5）。

「最終更新」は、書いた瞬間に**データについての主張**になる。
だが入れていたのは `generated_at`、**こちらがページを作り直した日**だった。

実測すると、CI が 2026-09-18 に触った `cho/*.html` は 850 枚。
そのうち **850 枚が日付の行だけの差**で、中身は1件も変わっていなかった。
名乗りは 100% 外れていた。直し方は主語を変えること。

    ❌ 最終更新　2026-09-18
    ✅ 対象期間　2018-2025年の認知件数
       このページを作り直した日　2026-09-18（数字が変わったとは限りません）

**見張りは3段。1段でも欠けると通る壊し方がある。**

  ① 表と定数を読む   … 名前と組み立てが正しいか（語ではなく構造を見る）
  ② 実データを見る   … 出すと決めた値が、全部のデータに在るか
  ③ 出来上がりを読む … 作り直したものに、直した文面が実際に出ているか

**③が無いと「テンプレを直したのに作り直していない」を通す。**
①②はソースとデータしか見ないので、出力が古いままでも黙る。

①を語句狩りにしない。直した経緯を書いたコメントが引っかかる
（実際に3か所引っかかった）。**語を数えると、語について書けなくなる。**

**捕まえないもの。**

  ・日付の値そのものが正しいか。名乗りの向きだけを見る
  ・JS が埋めた後の画面。静的なHTMLからは見えないので、①で script を読む
  ・`period` の中身が本当にその年のデータか。在ることしか見ていない
  ・`date` が何を指すべきか。**共通仕様6節の話で、ここでは決められない。**
    `period` から導けているかだけを見ている
  ・手書きの `policy.html`。作り直さないので③の対象にならない
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

sys.stdout.reconfigure(encoding="utf-8")

ROOT = config.ROOT
# 出力に出てはいけない名乗り。**こちらの都合を、データの事実として書いたもの。**
NG_DEYO = ("最終更新", "更新日", "更新頻度")


def dan1_teisu():
    """① 表と定数。名前と組み立てを読む。語は数えない。"""
    bad = []
    src = (ROOT / "scripts" / "pages.py").read_text(encoding="utf-8")
    m = re.search(r"def notes\(([^)]*)\)", src)
    if not m:
        bad.append("pages.py の notes() が見つからない")
    else:
        args = [a.split("=")[0].strip() for a in m.group(1).split(",")]
        # 主語の曖昧な名前を、引数として受けない。名前が文面になる
        if "updated" in args:
            bad.append("notes() が `updated` で受けている。"
                       "誰が更新したのか決まらない名前（built_on / period に分ける）")
        for want in ("built_on", "period"):
            if want not in args:
                bad.append(f"notes() が `{want}` を受けていない。{args}")

    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    for slot in ("built-on", "period"):
        if f'id="{slot}"' not in idx:
            bad.append(f"index.html に id=\"{slot}\" の欄が無い")
        if f'$("{slot}").textContent' not in idx:
            bad.append(f"index.html の script が #{slot} を埋めていない。"
                       "欄だけ置くと「—」のまま出る")
    return bad, "pages.py の notes() と index.html の script"


def dan2_data():
    """② 実データ。出すと決めた値が、全部に在るか。"""
    bad = []
    bi = json.loads((config.BUILD / "index.json").read_text(encoding="utf-8"))
    if not bi.get("period"):
        bad.append("data/build/index.json に period が無い。トップが「—年」と出す")
    mita = 1
    for c in bi["cities"]:
        fc = json.loads((config.BUILD / f'{c["code"]}.geojson').read_text(encoding="utf-8"))
        mita += 1
        got = fc["properties"].get("period")
        if not got:
            bad.append(f'{c["name"]} の geojson に period が無い。町丁目ページが「—年」と出す')
        elif got != bi.get("period"):
            # 揃っていないと、ページごとに違う期間を名乗る
            bad.append(f'{c["name"]} の period が {got}。index.json は {bi.get("period")}')

    # `date` は**その日に何かが起きた日ではない。** 期間の終わりを埋めただけ。
    # 窓を1年ずらしたとき、ここが置き去りになると「2025年のこと」と読まれる。
    # period から導けているかだけを見る（値の意味は共通仕様6節の話）。
    rec = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    zure = []
    for r in rec["records"]:
        owari = (r.get("period") or "").split("-")[-1]
        if not owari or r.get("date") != f"{owari}-12-31":
            zure.append(f'{r["id"]} date={r.get("date")} period={r.get("period")}')
    if zure:
        bad.append(f"date が period の終わりから導けていない record が {len(zure):,} 件: "
                   f"{zure[:2]}")
    return bad, f"index.json と geojson {mita} 件・record {len(rec['records']):,} 件"


def dan3_shutsuryoku():
    """③ 出来上がり。**作り直したものを読む。**

    ここが無いと、テンプレを直して作り直さなかったときに全部通る。
    """
    bad = []
    pages = sorted((ROOT / "cho").glob("*.html"))
    if not pages:
        return ["cho/*.html が無い。作り直していない"], "cho/*.html 0 件"
    for f in pages:
        t = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT).as_posix()
        for ng in NG_DEYO:
            if ng in t:
                bad.append(f"{rel} に「{ng}」が出ている。主語がこちらだと分かる言い方にする")
        if not re.search(r"対象期間　\d{4}-\d{4}年", t):
            bad.append(f"{rel} に対象期間が出ていない（作り直していない可能性）")
        if not re.search(r"作り直した日　\d{4}-\d{2}-\d{2}", t):
            bad.append(f"{rel} に作り直した日が出ていない（作り直していない可能性）")
        if bad:
            break          # 850枚すべてに同じことを言わない。1枚で足りる
    return bad, f"cho/*.html {len(pages)} 件"


def main():
    print("■ 日付の名乗り（3段）")
    ng = 0
    for i, (fn, name) in enumerate(
            [(dan1_teisu, "表と定数"), (dan2_data, "実データ"),
             (dan3_shutsuryoku, "出来上がり")], 1):
        bad, mita = fn()
        print(f"  ({i}) {name:<8} {mita}")
        for b in bad:
            print(f"      ★ {b}")
        ng += len(bad)
    if ng:
        print(f"  名乗りが外れているところが {ng} か所ありました。")
        return 1
    print("  3段とも通りました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
