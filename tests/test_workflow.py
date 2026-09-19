"""workflow の run ブロックが、shell として読めるか（共通仕様9節）。

2026-09-19：検査を1本足したとき、行継続を `\\` にしていた。
bash では行継続にならず、`for` の一覧が途中で切れて構文エラーになった。
**「4本通った」と報告しながら、workflow では0本走っていなかった。**

`cat -A` で見るまで気づかなかった。**目で読むと `\` と `\\` は同じに見える。**
目で読んで同じに見えるものは、何度読み直しても見つからない。機械で見る。

**この検査が捕まえられる形には限りがある。**
一覧や制御構文の途中で切れた形は捕まる。しかし

    python3 a.py \\
      --verbose

は bash では合法（「バックスラッシュという引数」として読まれる）ので鳴らない。
**見張りは、知っている形しか見つけられない。**
"""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# `run: |` のブロックを拾う。YAML の構文解析はしない（標準ライブラリに無い）。
RUN = re.compile(r"^(\s+)run: \|\s*\n((?:\1  .*\n|\s*\n)+)", re.M)


def run_blocks(text):
    """(行番号, 中身) の一覧。インデントは落とす。"""
    out = []
    for m in RUN.finditer(text):
        indent = len(m.group(1)) + 2
        body = "\n".join(l[indent:] if len(l) > indent else ""
                         for l in m.group(2).splitlines())
        out.append((text[:m.start()].count("\n") + 1, body))
    return out


class TestWorkflowShellParses(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash が無いので飛ばす")
        if not WORKFLOWS:
            self.skipTest("workflow が無い")

    def test_every_run_block_parses(self):
        checked = 0
        for wf in WORKFLOWS:
            text = wf.read_text(encoding="utf-8")
            blocks = run_blocks(text)
            self.assertGreater(len(blocks), 0, f"{wf.name} に run ブロックが無い")
            for line, body in blocks:
                r = subprocess.run(["bash", "-n"], input=body,
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0,
                                 f"{wf.name}:{line} の run が shell として読めない\n{r.stderr}")
                checked += 1
        self.assertGreater(checked, 0)

    def test_no_double_backslash_continuation(self):
        """`\\` で終わる行が無いこと。**bash では行継続にならない。**

        bash -n で捕まるのは一覧や制御構文の途中だけなので、
        形のほうも直接見る。
        """
        for wf in WORKFLOWS:
            for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
                self.assertFalse(line.rstrip().endswith("\\\\"),
                                 f"{wf.name}:{i} が `\\\\` で終わっている: {line.strip()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
