"""公開する木に入るファイルだけを数える。検査はこれを使う。

**除外リストだけで決めない。** 新しい作業用ディレクトリができるたびに漏れる。
実際 workflow が金庫を `_raw/` に展開したとき、検査が金庫まで見て落ちた
（2026-09-18・月次の初回実行）。金庫の記録には親の合計が正当に入っている。
検査が正しく、当て方が違っていた。

条件は2つ。**両方要る。**

  1. git が追跡している … 作業用に置いただけのものを落とす（許可リスト）
     `_raw/` も `__pycache__` も `data/raw/` も、追跡対象ではないので自動で落ちる。
     将来どんな一時ディレクトリができても、足し忘れが起きない
  2. 公開対象である   … 金庫にだけ置くものを落とす（`make_public_tree.EXCLUDE`）
     金庫では `docs/` も `CLAUDE.md` も追跡されている。そこには親の合計が
     **正当に**ある。1だけだと金庫で誤検出する

公開用の木は `git init` の前に検査する。そこでは追跡情報が無いので、
そのときだけ 1 を諦めて除外リストに落ちる。
**落ちたことを黙らない。** どちらで決めたかを返す。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

# git の追跡情報が取れないときの受け皿。木の中で走らせるときに使う。
# **ここに足すのは応急処置。** 本筋は git ls-files のほう。
FALLBACK_DENY = ("_raw/", "data/raw/", ".git/", "__pycache__/")


def _tracked():
    """git が追跡しているファイル。取れなければ None。"""
    r = subprocess.run(["git", "-C", str(config.ROOT), "ls-files", "-z"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return [p for p in r.stdout.split("\0") if p]


def _excluded():
    """公開しないと決めたもの（make_public_tree の除外リスト）。"""
    try:
        import make_public_tree
    except ImportError:
        return ()
    return tuple(path for path, _ in make_public_tree.EXCLUDE)


def _under(rel, paths):
    return any(rel == q or rel.startswith(q + "/") for q in paths)


def published(suffixes=None):
    """公開する木に入るファイルの相対パス。(paths, どう決めたか) を返す。"""
    rels = _tracked()
    how = "git が追跡しているもの"
    if rels is None:
        how = "除外リスト（git の追跡情報が無い）"
        rels = [p.relative_to(config.ROOT).as_posix()
                for p in config.ROOT.rglob("*") if p.is_file()]
        rels = [r for r in rels if not any(r.startswith(d) or f"/{d}" in r
                                           for d in FALLBACK_DENY)]
    skip = _excluded()
    out = [r for r in rels if not _under(r, skip)]
    if suffixes:
        out = [r for r in out if Path(r).suffix in suffixes]
    return sorted(out), how
