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


STEP = re.compile(r"^(\s+)- name: (.+)$", re.M)


def steps(text):
    """(段の名前, 段の本文) を上から順に。YAML は解かず、`- name:` で区切る。"""
    marks = list(STEP.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(2).strip(), text[m.start():end]))
    return out


class TestFetchOrder(unittest.TestCase):
    """取得は1系統。**金庫にしまえた観測だけを公開する**（2026-09-23）。

    順番：取ってくる → 生データと観測の記録を金庫にしまう → 作る
          → 加工の記録を金庫にしまう → もう一度検査する → 公開側に入れる
    """

    KOUSHIN = ROOT / ".github" / "workflows" / "koushin.yml"
    ORDER = ["取ってくる", "生データと観測の記録を金庫にしまう", "作る",
             "加工の記録を金庫にしまう", "もう一度検査する（作ったものに対して）", "結果をしまう"]

    def setUp(self):
        self.steps = steps(self.KOUSHIN.read_text(encoding="utf-8"))
        self.by_name = dict(self.steps)

    def test_order(self):
        names = [n for n, _ in self.steps]
        for n in self.ORDER:
            self.assertIn(n, names, f"段「{n}」が無い")
        idx = [names.index(n) for n in self.ORDER]
        self.assertEqual(idx, sorted(idx), f"段の順番が違う: {[names[i] for i in sorted(idx)]}")

    def test_raw_is_saved_even_if_fetch_failed(self):
        """取れなかった回も、取れた分と観測の記録は金庫にしまう。"""
        body = self.by_name["生データと観測の記録を金庫にしまう"]
        self.assertIn("!cancelled()", body)
        self.assertIn("steps.fetch.outcome", body)
        self.assertIn("id: fetch", self.by_name["取ってくる"])
        self.assertIn("data/raw/manifest", body)

    def test_publishing_steps_do_not_run_after_a_failure(self):
        """`if:` を付けると、前の段が落ちても走りうる。公開までの段には付けない。"""
        for n in self.ORDER[2:]:
            self.assertNotRegex(self.by_name[n], r"\n\s+if:",
                                f"「{n}」に if: がある。金庫にしまえなかった観測で走りうる")

    def test_both_vault_steps_confirm_the_push(self):
        for n in ("生データと観測の記録を金庫にしまう", "加工の記録を金庫にしまう"):
            self.assertIn("kinko_shimau.sh", self.by_name[n])
        script = (ROOT / "scripts" / "kinko_shimau.sh").read_text(encoding="utf-8")
        self.assertIn("merge-base --is-ancestor HEAD origin/main", script)
        if shutil.which("bash"):
            r = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "kinko_shimau.sh")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_only_one_workflow_fetches(self):
        fetchers = [wf.name for wf in WORKFLOWS
                    if "fetch_data.py" in wf.read_text(encoding="utf-8")]
        self.assertEqual(fetchers, ["koushin.yml"])

    def test_public_commit_refuses_raw_and_zip(self):
        body = self.by_name["結果をしまう"]
        self.assertIn("data/raw/", body)
        self.assertIn(r"\.zip$", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
