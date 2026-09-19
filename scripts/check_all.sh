#!/bin/sh
# 検査を全部走らせる。**ここが唯一の一覧。**
#
# 前は workflow に2ブロック、手元のループに1つ、同じ並びが3か所あった。
# 1本足したとき、そのうち1か所の行継続を壊して構文エラーになった
# （2026-09-19。10/2 の月次は検査ステップで落ちていた）。
#
# 守ること2つ。
#   ・**最初の1件で止めない。** 止めると2件目以降が隠れる（共通仕様9節）
#   ・**飛ばしたことを黙らない。** 出さないと、0本通ったのと
#     全部通ったのが同じ見た目になる（共通仕様9節）
#
# 行継続（\）を使わない。1本足すたびに壊れる形にしない。

set -u
GROUP=${GROUP:-}          # GitHub Actions のときだけ ::group:: を出す

checks="-m|unittest|discover|-s|tests
scripts/check_small_counts.py
scripts/check_copy.py
scripts/check_headings.py"

ng=0
echo "$checks" | while IFS= read -r line; do
  c=$(echo "$line" | tr '|' ' ')
  [ -n "$GROUP" ] && echo "::group::$c"
  if out=$(python3 $c 2>&1); then st="通過"; else st="★ 落ちた"; echo "$c" >> /tmp/.check_ng; fi
  [ -n "$GROUP" ] && echo "$out"
  [ -n "$GROUP" ] && echo "::endgroup::"
  skipped=$(echo "$out" | grep -o 'skipped=[0-9]*' | head -1)
  if [ -n "$skipped" ]; then
    note=" ▲ 飛ばした検査がある（$skipped）"
    [ -n "$GROUP" ] && echo "::warning::$c で飛ばした検査がある（$skipped）"
  else
    note=""
  fi
  printf '  %-34s %s%s\n' "$c" "$st" "$note"
done

if [ -f /tmp/.check_ng ]; then
  echo "検査が落ちた。★ の付いたものを全部見ること"
  rm -f /tmp/.check_ng
  exit 1
fi
echo "  検査は全部通った"
