"""門（common/kado.py）を回り込む道を作っていないかの見張り（構造の検査）。

**名前の一覧では作らない。** 足した日に黙る（共通仕様4節）。置き場の木を
`.py` の拡張子とファイルの中身だけで歩いて、2つを見る。

  1. `common/kado.py` 以外の `.py` に、門を回り込む道具の語が出ていないか
     （出ていても、その行に `# kado-soto: <理由>` があれば、自分のサイトを見る
     だけの行として許す）。
  2. `urlopen(` か `urllib.request` を使う `.py` は、`common/kado.py` を
     import しているか（直接でも、別のモジュール経由でもよい。経由のときは、
     経由先が kado を import していることまで辿って確かめる）。

歩く木から除くもの：

    .git／__pycache__／node_modules   どの階でも除く。コードではない
    tests/（上の階だけ）              検査そのもの。偽の相手（Nise 等）が
                                      urllib.request.BaseHandler を使うのは
                                      意図した形で、本物の通信はしていない
    data/／inbox/／_raw/（上の階だけ） 生データ・参照データ・金庫を展開した先。
                                      `.py` は無い想定で、歩いても意味が無い

**壊して鳴ることを確かめる。** `TestKadoKouzouBreaksWhenExpected` が、一時フォルダに
作った小さな置き場（本物は書き換えない）で、違反を1つ入れると検査の中身
（`scan_mawarikomu` / `scan_urlopen_without_kado`）が拾うことを確かめる。
"""

import ast
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KADO_REL = "common/kado.py"

DAME_TOP = {"tests", "data", "inbox", "_raw"}       # 上の階だけ除く
DAME_ANYWHERE = {".git", "__pycache__", "node_modules"}  # どの階でも除く

MAWARIKOMU = re.compile(
    r"build_opener|http\.client|HTTPSConnection|HTTPConnection"
    r"|\brequests\b|\burllib3\b|socket\.create_connection"
    r"|urlopen\([^)]*context\s*="
    r"|cafile\s*=")
KADO_SOTO = re.compile(r"#\s*kado-soto\s*:")


def py_files(root):
    """置き場の木を `.py` だけ歩く。root からの相対パス（`/` 区切り）で返す。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DAME_ANYWHERE]
        if Path(dirpath) == Path(root):
            dirnames[:] = [d for d in dirnames if d not in DAME_TOP]
        for name in filenames:
            if name.endswith(".py"):
                out.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(out)


def scan_mawarikomu(root, files):
    """`common/kado.py` 以外の行に、回り込む道具の語が出ていないか。

    返り値は (ファイル, 行番号, その行) の並び。空なら問題なし。
    """
    ng = []
    for rel in files:
        if rel == KADO_REL:
            continue
        text = (Path(root) / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if MAWARIKOMU.search(line) and not KADO_SOTO.search(line):
                ng.append((rel, i, line.strip()))
    return ng


def _module_names(text):
    """import 文に出てくる名前を集める（`import X` と `from X import Y` のどちらも）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
                names.update(a.name.split("."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(node.module.split("."))
            for a in node.names:
                names.add(a.name)
                names.add(f"{node.module}.{a.name}")
    return names


def imports_kado(root, rel, files, seen=None):
    """`common/kado.py` を、直接か経由で import しているか。

    経由のときは、経由先（同じファイル名を持つ `.py`。置き場の中で名前で探す）を辿り、
    そこが kado を import しているかまで確かめる。
    """
    seen = seen if seen is not None else set()
    if rel in seen:
        return False
    seen.add(rel)
    text = (Path(root) / rel).read_text(encoding="utf-8")
    names = _module_names(text)
    if "kado" in names or "common.kado" in names:
        return True
    for n in names:
        tail = n.rsplit(".", 1)[-1]
        for cand in files:
            if cand != rel and Path(cand).stem == tail:
                if imports_kado(root, cand, files, seen):
                    return True
    return False


def scan_urlopen_without_kado(root, files):
    """`urlopen(` か `urllib.request` を使うのに、門を import していない `.py`。

    返り値はファイルの一覧。空なら問題なし。
    """
    ng = []
    for rel in files:
        if rel == KADO_REL:
            continue
        text = (Path(root) / rel).read_text(encoding="utf-8")
        if "urlopen(" in text or "urllib.request" in text:
            if not imports_kado(root, rel, files):
                ng.append(rel)
    return ng


class TestKadoKouzou(unittest.TestCase):
    """本物の置き場を見る。"""

    def setUp(self):
        self.files = py_files(ROOT)

    def test_files_are_found(self):
        """見た .py の数が0なら、歩き方そのものが壊れている。"""
        self.assertGreater(len(self.files), 0)
        self.assertIn(KADO_REL, self.files)
        self.assertIn("scripts/fetch_data.py", self.files)

    def test_tests_and_data_are_excluded_only_at_the_top(self):
        self.assertFalse(any(f.startswith("tests/") for f in self.files))
        self.assertFalse(any(f.startswith("data/") for f in self.files))

    def test_no_bypass_tool_outside_kado(self):
        ng = scan_mawarikomu(ROOT, self.files)
        self.assertEqual(ng, [], "\n".join(f"{r}:{i}  {line}" for r, i, line in ng))

    def test_urlopen_users_import_kado(self):
        ng = scan_urlopen_without_kado(ROOT, self.files)
        self.assertEqual(ng, [], f"urlopen・urllib.request を使うのに、門（common/kado.py）"
                                 f"を import していない: {ng}")


class TestKadoKouzouBreaksWhenExpected(unittest.TestCase):
    """**壊して鳴ることを確かめる。** 一時フォルダに、common/kado.py の形だけ真似た
    小さな置き場を作り、そこに違反を1つ入れると `scan_*` 関数が拾うことを確かめる。
    本物の置き場（common/・scripts/）は書き換えない。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "common"))
        os.makedirs(os.path.join(self.tmp, "scripts"))
        self._kaku("common/kado.py", "TIMEOUT = 1\n")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _kaku(self, rel, text):
        Path(self.tmp, rel).write_text(text, encoding="utf-8")

    def test_build_opener_outside_kado_is_caught(self):
        self._kaku("scripts/a.py", "import urllib.request\n"
                                   "op = urllib.request.build_opener()\n")
        ng = scan_mawarikomu(self.tmp, py_files(self.tmp))
        self.assertTrue(ng, "build_opener を足したのに、検査が鳴らなかった")

    def test_kado_soto_marked_line_is_allowed(self):
        self._kaku("scripts/a.py",
                   "import urllib.request  # kado-soto: 自分のサイトを見るだけ\n"
                   "op = urllib.request.build_opener()  # kado-soto: 自分のサイト\n")
        ng = scan_mawarikomu(self.tmp, py_files(self.tmp))
        self.assertEqual(ng, [], "kado-soto の印を付けた行まで落とした")

    def test_urlopen_without_kado_import_is_caught(self):
        self._kaku("scripts/b.py", "import urllib.request\n"
                                   "urllib.request.urlopen('https://example.invalid')\n")
        ng = scan_urlopen_without_kado(self.tmp, py_files(self.tmp))
        self.assertIn("scripts/b.py", ng, "門を import していないのに、検査が鳴らなかった")

    def test_urlopen_with_direct_kado_import_passes(self):
        self._kaku("scripts/c.py", "from common import kado\n"
                                   "import urllib.request\n"
                                   "urllib.request.urlopen('https://example.invalid')\n")
        ng = scan_urlopen_without_kado(self.tmp, py_files(self.tmp))
        self.assertEqual(ng, [])

    def test_urlopen_with_indirect_kado_import_passes(self):
        """common.fetch のような経由先。経由先が kado を import していれば許す。"""
        self._kaku("scripts/d.py", "import fetch_wrap\n"
                                   "import urllib.request\n"
                                   "urllib.request.urlopen('https://example.invalid')\n")
        self._kaku("scripts/fetch_wrap.py", "from common import kado\n")
        ng = scan_urlopen_without_kado(self.tmp, py_files(self.tmp))
        self.assertEqual(ng, [], "経由先が kado を import しているのに、検査が鳴った")

    def test_zero_files_means_the_walk_is_broken(self):
        empty = tempfile.mkdtemp()
        try:
            self.assertEqual(py_files(empty), [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
