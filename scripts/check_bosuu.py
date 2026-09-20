#!/usr/bin/env python3
"""件数の線が母数を見ているか（共通仕様3.2）。

**升の線が2本あって、片方だけ母数を見ていた。**

    率     人口 500 人未満なら出さない   ← 母数を見ている
    件数   2 件以下なら伏せる            ← 母数を見ていない

県が公表しているのは**1件ずつの記録**（発生年月日・発生時・町丁目・手口）で、
**町丁目別の件数は出していない。** 8年ぶんを足し、手口を2層に束ねているのは
こちら。**こちらが作った形なので、伏せる線もこちらが決める**（共通仕様3.2 の(B)）。

**ただし率と同じ数字は使わない。** 守っているものが違う。

    MIN_POPULATION = 500  … 率が跳ねるので率を出さない（読み違いを防ぐ）
    TOKUTEI_FLOOR  = 100  … 住民に結びつきうるので件数を伏せる（特定を防ぐ）

500 をそのまま件数に当てると、人口 200〜499 で件数 3〜5 の升が 61 消える。
**300 人の町の 3 件は誰も特定しない。** 結びつくのは人口 100 未満の升。

**向きをまちがえないこと。** 「人口が小さいほど伏せる」を素直に当てると、
**もっとも特定に結びつきにくい升を消す。**
人口 0 の町丁目に件数 98 があるが、住んでいる人がいないので、
被害者は住民ではない（駅前・商業地に停めた人）。結びつくのは
「人口が小さい**かつ**件数も小さい」升。

**捕まえないもの。**

  ・人口 0 の町丁目。住民がいないので、住民に結びつかない。**伏せない**
  ・件数が人口を超えている升。被害者が住民でない証拠。**伏せない**
  ・伏せた升（`None`）。もう出していないので、ここでは見ない
  ・率の線そのもの。あちらは読み違いを防ぐ線で、ここでは触らない
  ・まとまりの余裕（`C(k,m)`）。伏せる升が増えると k が増えて余裕は広がるが、
    **札の意味は変わる。** 「1-2」と書けなくなる升が出る。
    そこは `check_small_counts` の仕事
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

sys.stdout.reconfigure(encoding="utf-8")

# 住民に結びつきうる人口。**率の線（500）とは別の数字。守るものが違う。**
TOKUTEI_FLOOR = 100
# その人口帯で伏せたい件数の上限。いまは 2（母数を見ていない）。
TOKUTEI_MAX = 5


def main():
    bi = json.loads((config.BUILD / "index.json").read_text(encoding="utf-8"))
    abunai, nokosu, mita = [], 0, 0
    for c in bi["cities"]:
        fc = json.loads((config.BUILD / f'{c["code"]}.geojson').read_text(encoding="utf-8"))
        P = fc["properties"]
        for f in fc["features"]:
            p = f["properties"]
            pop = p["jinko"] or 0
            for i, n in enumerate(p["n"]):
                if n is None:
                    continue              # もう伏せている
                mita += 1
                if not n:
                    continue              # 0件。伏せない
                if pop == 0 or n > pop:
                    nokosu += 1           # 被害者が住民でない。伏せない
                    continue
                if pop < TOKUTEI_FLOOR and n <= TOKUTEI_MAX:
                    abunai.append((c["name"], p["name"],
                                   P["layers"][i]["name"], pop, n))

    print(f"■ 件数の線が母数を見ているか   見た升 {mita:,}")
    print(f"   伏せない升（人口0・件数が人口超）{nokosu:,}"
          f"  … 住民がいない／被害者が住民でない")
    if not abunai:
        print(f"   人口 {TOKUTEI_FLOOR} 人未満で件数 {TOKUTEI_MAX} 以下の升は"
              "ありませんでした。")
        return 0
    print(f"\n   ★ 人口 {TOKUTEI_FLOOR} 人未満なのに件数をそのまま出している升"
          f" {len(abunai)} 件")
    for city, name, layer, pop, n in sorted(abunai, key=lambda r: (r[3], r[4]))[:8]:
        print(f"      {city} {name}（{layer}）  人口 {pop:>3}  件数 {n}")
    if len(abunai) > 8:
        print(f"      … ほか {len(abunai) - 8} 件")
    print(f"\n   件数の線が母数を見ていない。"
          f"人口 {TOKUTEI_FLOOR} 人未満では {TOKUTEI_MAX} 件以下も伏せること。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
