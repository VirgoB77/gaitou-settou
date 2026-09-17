"""突合率を確かめ、繋がらなかった行を unmatched.csv に残す。

捨てない。捨てると、どの町丁目が地図から抜けているか追えなくなる。
"""

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
import police

sys.stdout.reconfigure(encoding="utf-8")

C = config.COLS


def main():
    config.BUILD.mkdir(parents=True, exist_ok=True)
    out = config.BUILD / "unmatched.csv"
    total_rows = total_bad = 0

    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["年", "元ファイル", "手口", "市区町村", "町丁目", "正規化後", "理由",
                    "発生年月日", "発生時"])

        for c in config.CITIES:
            bd = police.load_boundary(c["code"])
            print(f"■ {c['name']}  境界 {len(bd)} 区画"
                  f"（HCODE対象外 {bd.skipped} 件を除外／同名統合 {len(bd.merged)} 件）")

            for year in config.YEARS:
                rows = police.load(c["code"], year)
                if not rows:
                    print(f"    {year}年  CSVなし")
                    continue
                reasons, bad = Counter(), []
                for r in rows:
                    area, reason, key = police.match(r, bd)
                    reasons[reason] += 1
                    if area is None:
                        bad.append((r, reason, key))

                ok = len(rows) - len(bad)
                total_rows += len(rows)
                total_bad += len(bad)
                print(f"    {year}年  {len(rows):>5} 行  突合 {ok / len(rows):.2%}"
                      f"  （突合できず {len(bad)} 行 {len(bad) / len(rows):.2%}）")
                for reason, n in reasons.most_common():
                    if reason != "そのまま一致":
                        print(f"          {n:>4} 行  {reason}")

                for r, reason, key in bad:
                    w.writerow([year, r["_file"], r[C["teguchi"]], r[C["city"]],
                                r[C["cho"]], key, reason, r[C["date"]], r[C["hour"]]])
            print()

    print(f"合計 {total_rows:,} 行中 {total_bad} 行が突合できず（{total_bad / total_rows:.2%}）")
    print(f"→ {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
