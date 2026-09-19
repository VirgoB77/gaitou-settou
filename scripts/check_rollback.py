"""公開側に出す前に、**巻き戻していないか**を見る。

CI が定期実行で生成物を作り直して公開側に commit する。金庫は生データしか
受け取らないので、金庫の生成物は CI が走るたびに古くなる。
そこから作り直して `git add -A` すると、**CI が昨日やった仕事を古い日付で
上書きする。** 消えるのは設定ではなくデータで、次の定期実行まで古いまま出る。

公開側の作業木で、commit の**前**に走らせる。

    cd <公開用の作業木> && python scripts/check_rollback.py

見るものは3つ。

  1. 生成日が戻っていないか（これが本体）
  2. コードと生成物の件数の釣り合い（気づく入口）
     コードしか触っていないのに生成物が何百件も stage されていたら、
     動いているのは中身ではなく日付。
  3. **公開側に出るものが混ざっているか**（写す必要があるかの判断）
     金庫だけの変更なら、公開側に写す手順そのものが要らない。

3つめは「作り直して比べる」では答えられない。金庫の生成物は CI より
古いので、**作り直すと日付だけで必ず何百件も差が出る**（2026-09-19 に実測。
850枚すべてが `最終更新 2026-09-18 → 2026-09-17` の1行だけ違う）。
比べる相手のほうが汚れているので、**そもそも比較で答える問いではない。**
追跡情報と除外リストから決める。

**捕まえないもの。**

  ・生成日が同じまま、中身だけ古いもの。日付しか見ていない
  ・CI が作っていない生成物（手で置いたもの）
  ・件数の釣り合いは**落とさない**。正当な作り直しでも件数は増えるため、
    落とすと狼少年になる。目を向けさせるだけ
  ・公開側に出るかどうかは**範囲だけ**。出ると分かっても、中身は見ない

日付が動いていなければ通る。**「通った＝中身も新しい」ではない。**
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tracked

sys.stdout.reconfigure(encoding="utf-8")

# 生成物。CI が書き戻す側。
BUILT = ("cho/", "data/build/", "index.json", "sitemap.xml", "robots.txt")
# 生成日を持つファイル。ここが戻ったら巻き戻し。
DATED = ("data/build/index.json", "index.json")


def _show(ref, path):
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _generated_at(text):
    if not text:
        return None
    try:
        return json.loads(text).get("generated_at")
    except (json.JSONDecodeError, AttributeError):
        return None


def staged():
    r = subprocess.run(["git", "diff", "--cached", "--name-only"],
                       capture_output=True, text=True, check=True)
    return [p for p in r.stdout.splitlines() if p]


def main():
    files = staged()
    if not files:
        print("stage されているものがありません。")
        return 0

    built = [f for f in files if f.startswith(BUILT)]
    code = [f for f in files if not f.startswith(BUILT)]
    print(f"■ stage されているもの  生成物 {len(built)} 件／それ以外 {len(code)} 件")

    # 公開側に写す必要があるか。**作り直して比べるのでは答えられない**（上の説明）。
    pub, how = tracked.published()
    deru = [f for f in files if f in set(pub)]
    if deru:
        print(f"   公開側にも出るもの {len(deru)} 件（{how}）: {deru[:5]}")
        print("   → 公開用リポジトリにも写すこと")
    else:
        print(f"   公開側に出るものはありません。金庫だけの変更（{how}）")

    bad = []
    for path in DATED:
        if path not in files:
            continue
        old = _generated_at(_show("HEAD", path))
        new = _generated_at(_show("", path))     # "" は index（stage 済みの中身）
        if old and new and new < old:
            bad.append(f"{path} の生成日が {old} → {new} に戻っている")
        elif old and new:
            print(f"   {path}  生成日 {old} → {new}")

    # 気づく入口。落としはしないが、目を向けさせる。
    if built and not code:
        print("   （生成物だけの更新。CI の出力を出し直しているなら、日付を確かめること）")
    elif code and len(built) > 50:
        print(f"   ▲ コードを {len(code)} 件しか触っていないのに、生成物が {len(built)} 件ある。")
        print("      中身ではなく日付が動いている可能性がある。1件開いて確かめること。")

    if bad:
        print()
        for b in bad:
            print(f"  ★ {b}")
        print("  CI が作ったものを古い日付で上書きしようとしています。")
        print("  コードだけを直すなら、コードのパスだけを stage してください。")
        return 1
    print("  巻き戻していません。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
