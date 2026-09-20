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
#
# **捕まえないもの。**
#   ・`scripts/check_*.py` という名前でない検査。ここも形で拾っているが、
#     拾う形は「名前」。別の置き場に作られたら気づかない
#   ・一覧に在って、中身が空の検査。走ったことしか見ていない

set -u
GROUP=${GROUP:-}          # GitHub Actions のときだけ ::group:: を出す

checks="-m|unittest|discover|-s|tests
scripts/check_small_counts.py
scripts/check_copy.py
scripts/check_headings.py
scripts/check_nanori.py
scripts/check_bosuu.py"

# **一覧に書き忘れたものを、一覧で探さない。**
# 名前で並べた見張りは、名前を増やした日に黙る（共通仕様4節）。
# 実際に確かめた。落ちる検査を隣に置いて一覧に書かなかったら、
# 「検査は全部通った」と出た（2026-09-19）。
#
# scripts/check_*.py を**全部拾って**、一覧にも下の除外にも無ければ落とす。
# 走らせないと決めるのは自由。**黙って外すのを許さない。**
hazusu="scripts/check_rollback.py|commit の前に手で走らせる（stage された中身を見るので、CI では見るものが無い）"

for f in scripts/check_*.py; do
  [ -e "$f" ] || continue
  case "
$checks" in *"
$f"*) continue;; esac
  case "$hazusu" in *"$f|"*) continue;; esac
  echo "  ★ $f が一覧にも除外にも無い。走らせるか、理由を書いて外すこと"
  echo "$f" >> /tmp/.check_ng
done

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
