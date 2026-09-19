"""見出しが「そのデータが何であるか」を書いているかを見る（共通仕様3.3の隣）。

    そのデータが何であるかを書く。何に使えるかではなく。

見出し・タイトル・説明文だけを見る。**本文は見ない。**
本文には読者を書いてよい（「住まいを探している人のために作りました」は
できることであって用途ではない）。縁が細いので、当てる場所を絞る。

**`<script>` と `<style>` の中を先に落とす。**
落とさないと、JS が組み立てる文字列の中の `<h2>…</h2>` を拾い、
直す必要のないものを直しにいく。
**誤報を出す見張りは、そのうち誰も見なくなる**（共通仕様9節）。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config
import tracked

sys.stdout.reconfigure(encoding="utf-8")

# 「何に使えるか」の言い回し。**見出しにだけ当てる。**
USE_WORDS = [
    "チェック", "調べられ", "調べよう", "探せ", "探そう", "選べ", "選ぼう",
    "比べられ", "分かります", "分かる", "役立", "便利",
    "おすすめ", "ランキング", "向けの", "のための",
]

TAG = re.compile(r"<(title|h1|h2|h3)\b[^>]*>(.*?)</\1>", re.S | re.I)
META = re.compile(r'<meta\s+name="description"\s+content="([^"]*)"', re.I)
DROP = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def headings(text):
    """見出し・タイトル・説明文。script と style を落としてから拾う。"""
    body = DROP.sub(" ", text)
    out = [(m.group(1).lower(), re.sub(r"<[^>]*>", "", m.group(2)).strip())
           for m in TAG.finditer(body)]
    out += [("description", m.group(1).strip()) for m in META.finditer(body)]
    return [(k, v) for k, v in out if v]


def main():
    rels, how = tracked.published({".html"})
    hits, total = [], 0
    for rel in rels:
        try:
            text = (config.ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind, value in headings(text):
            total += 1
            for w in USE_WORDS:
                if w in value:
                    hits.append(f"{rel}  <{kind}> 「{value}」  ← 「{w}」")

    print(f"■ 見出し・タイトル・説明文 {total:,} 件を調べた（{how}）")
    if hits:
        for h in hits[:20]:
            print(f"  ★ {h}")
        print(f"\n  「何に使えるか」を書いている見出しが {len(hits)} 件あります。")
        print("  そのデータが何であるかに書き換えてください（共通仕様3.3）。")
        return 1
    print("  どれも「そのデータが何であるか」を書いています。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
