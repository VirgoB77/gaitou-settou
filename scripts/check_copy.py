"""サイトの文面に、地域を評価する言葉が混じっていないかを調べる。

指示書の「絶対に守るルール」4番と、完成の判断基準の
「どの画面にも評価語が存在しない（コピー全文を検索して確認する）」に対応する。

見出し・本文・meta description・title・alt・aria-label のすべてを見る。
例外は置かない。1語でも出たら失敗として扱う。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
import tracked
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

# 地名と並ぶと、その土地への決めつけになる言葉。
NG = [
    "危険", "治安", "物騒", "不安", "安心", "安全", "警戒",
    "注意", "要注意", "気をつけ",
    "悪い", "悪化", "劣悪", "良い", "良好",
    "おすすめ", "オススメ", "お勧め",
    "ワースト", "ベスト", "避けた", "避ける",
]

# 数字の読み方に関わる言葉は、評価ではないので対象にしない。
# 凡例の「少ない／多い」、本文の「大きくなりやすい」など。

# 公開する木に入るものは全部見る。**.md と .py を外さない。**
# 外していたせいで CLAUDE.md の評価語13か所を長いあいだ見落としていた
# （2026-09-17）。GitHub Pages は .nojekyll があるとリポジトリの中身を
# そのまま配るので、.md も .py も画面と同じく人が読める。
TARGET_SUFFIX = {".html", ".css", ".xml", ".md", ".py", ".yml"}

# 自分自身は見ない。NG語を文字列として持っているので必ず当たる。
SELF = {"check_copy.py"}


def _under(rel, paths):
    """rel が paths のどれかそのもの、またはその下にあるか。"""
    return any(rel == q or rel.startswith(q + "/") for q in paths)


def not_published():
    """公開しないと決めたものは見ない。**手で書かず、除外リストから取る。**

    2つの一覧を別々に持つと必ず食い違う。実際 CI の許可リストと
    make_public_tree の除外リストが食い違っていた（2026-09-17）。

    公開用の木の中では make_public_tree 自身が入っていないので import に失敗する。
    そのときは何も外さない — 木の中には外すべきファイルがもう無いので、
    全部見るのが正しい。
    """
    try:
        import make_public_tree
    except ImportError:
        return ()
    # ディレクトリを外したら、その中身も外す。**完全一致にしない。**
    # 完全一致だと ".claude" は外れても ".claude/skills/..." が残り、
    # 「検査する範囲＝公開する範囲」が崩れる。
    return tuple(path for path, _ in make_public_tree.EXCLUDE)


def scan(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for word in NG:
        for m in re.finditer(re.escape(word), text):
            line = text.count("\n", 0, m.start()) + 1
            start = max(0, m.start() - 28)
            end = min(len(text), m.end() + 28)
            around = text[start:end].replace("\n", " ").strip()
            hits.append((line, word, around))
    return hits


def main():
    # scripts は外さない。公開する木に入るので、コメントも人が読める。
    # どれを見るかは tracked に任せる（git の追跡 ＋ 公開対象の2条件）。
    rels, how = tracked.published(TARGET_SUFFIX)
    files = [config.ROOT / r for r in rels if pathlib.Path(r).name not in SELF]
    total = 0
    for path in files:
        hits = scan(path)
        if hits:
            total += len(hits)
            print(f"■ {path.relative_to(config.ROOT)}")
            for line, word, around in hits:
                print(f"    {line}行目  「{word}」  …{around}…")

    print(f"\n調べたファイル {len(files)} 件（{how}）")
    if total:
        print(f"評価語が {total} か所ありました。直してください。")
        return 1
    print("評価語はありませんでした。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
