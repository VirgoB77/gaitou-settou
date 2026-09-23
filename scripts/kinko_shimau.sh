#!/bin/sh
# 金庫（private の <公開用>-raw）に commit して push し、**金庫の main に入ったことを確かめる。**
#
# 使い方（workflow で、金庫を展開した _raw の中から）:
#   sh ../scripts/kinko_shimau.sh "<commit の題>" <足す場所>...
#
# 変わりが無ければ commit しない（0 で終わる）。
# push が通ったと言っても、金庫の main に今回の commit が入っていなければ 1 で終わる。
# **ここで 1 が返ったら、その回の観測は公開しない**（workflow が次の段に進まない）。
#
# 生データの段と、加工の記録の段の2か所から呼ぶ。手順を2か所に書かない。
set -eu

msg=$1
shift

git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git add -- "$@"

# 作業中のファイル（scripts/fetch_data.py が付ける *.part）が混ざったら止まる。
# 金庫の .gitignore が外しているはずだが、外れていなければここで捕まえる
if git diff --cached --name-only | grep -E '\.part(/|$)'; then
  echo "作業中のファイルが混ざった。止まる"
  exit 1
fi

if git diff --cached --quiet; then
  echo "変わりなし（$*）"
  exit 0
fi

git commit -q -m "$msg"

pushed=
for i in 1 2 3 4 5; do
  if git push -q origin HEAD:main; then
    pushed=1
    break
  fi
  git pull -q --rebase --autostash -X theirs origin main || { git rebase --abort 2>/dev/null || true; exit 1; }
  sleep $((i * 3))
done
if [ -z "$pushed" ]; then
  echo "5回試したが金庫に送れなかった。止まる"
  exit 1
fi

git fetch -q origin main
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "金庫の main に今回の commit が入っていない。止まる"
  exit 1
fi
echo "金庫にしまえた: $(git rev-parse --short HEAD)  $msg"
