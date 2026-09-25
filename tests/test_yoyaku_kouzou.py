"""予約台帳（repo横断・同じ相手・同じ日本時間の日）の配線の見張り。

**名前の一覧では作らない。** `.github/workflows/` の workflow を全部読み、
`python3 <ファイル>.py` で走らせる `.py` のうち、門を始める（`hajimeru(`）ものを
走らせる job を見つける。その job が、その段より前に次をそろえているかを見る。

    ① 予約台帳（VirgoB77/kujiraya-aite-yoyaku）を、YOYAKU_DEPLOY_KEY で `_yoyaku` に出す段
       （continue-on-error を付けない。出せなければ、そこで止まる）
    ② YOYAKU_DIR・YOYAKU_REPO を GITHUB_ENV に書く段
    ③ RUN_DATE を書く段は、同じ段で RUN_HAJIME も書く（日付と開始時刻を同じ1つの時刻から取る）

ほかに見ること：`.gitignore` が `_yoyaku/` を外している。相手台帳が予約を要ると書いていて、
どの相手にも ASCII の固定IDがあり、重ならない。門が本番の置き場（yoyaku/）に書く。

**門は、配線が欠けていても止まる側に倒れる**（YOYAKU_DIR が無ければ、その相手へは行かない）。
ここで見るのは、「止まったまま、だれも気づかない」を防ぐため。
"""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from common import kado  # noqa: E402

YOYAKU_REPO = "VirgoB77/kujiraya-aite-yoyaku"
WF = os.path.join(HERE, ".github", "workflows")
PY_HASHIRASERU = re.compile(r"\bpython3?\s+(?:-\S+\s+)*([\w./-]+\.py)\b")


def _yomu(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def jobs(text):
    """workflow の文字から、job ごとの段（文字のかたまり）の並びを返す。{job名: [段, ...]}"""
    lines = text.replace("\r\n", "\n").split("\n")
    out, i = {}, 0
    while i < len(lines) and not re.match(r"^jobs:\s*$", lines[i]):
        i += 1
    job, dan, dan_indent = None, None, None
    for l in lines[i + 1:]:
        m = re.match(r"^  ([\w-]+):\s*$", l)
        if m:
            job, dan, dan_indent = m.group(1), None, None
            out[job] = []
            continue
        if job is None:
            continue
        if re.match(r"^\s{4}steps:\s*$", l):
            dan_indent = -1
            continue
        if dan_indent is None:
            continue
        m = re.match(r"^(\s*)- ", l)
        if m and (dan_indent == -1 or len(m.group(1)) == dan_indent):
            dan_indent = len(m.group(1))
            dan = [l]
            out[job].append(dan)
        elif dan is not None:
            dan.append(l)
    return {j: ["\n".join(d) for d in ds] for j, ds in out.items()}


def mon_wo_hajimeru(dan):
    """この段が走らせる `.py` のうち、門を始める（hajimeru）もの。"""
    hits = []
    for m in PY_HASHIRASERU.finditer(dan):
        p = os.path.join(HERE, m.group(1))
        if os.path.isfile(p) and "hajimeru(" in _yomu(p):
            hits.append(m.group(1))
    return hits


def yoyaku_wo_dasu(dan):
    return ("actions/checkout@" in dan and re.search(r"repository:\s*" + re.escape(YOYAKU_REPO) + r"\s*$", dan, re.M)
            and re.search(r"ssh-key:\s*\$\{\{\s*secrets\.YOYAKU_DEPLOY_KEY\s*\}\}", dan)
            and re.search(r"path:\s*_yoyaku\s*$", dan, re.M))


def yoyaku_wo_watasu(dan):
    return (re.search(r"YOYAKU_DIR=\S*_yoyaku\b", dan) and ("YOYAKU_REPO=" + YOYAKU_REPO) in dan
            and "GITHUB_ENV" in dan)


def hajime_wo_kaku(dan):
    return "RUN_HAJIME=" in dan and "RUN_DATE=" in dan and "GITHUB_ENV" in dan and "TZ=Asia/Tokyo" in dan


class 予約台帳の配線(unittest.TestCase):

    def test_門を始める段の前に_予約台帳と開始時刻がそろう(self):
        mitsuketa = 0
        for na in sorted(os.listdir(WF)):
            if not na.endswith((".yml", ".yaml")):
                continue
            for job, dans in jobs(_yomu(os.path.join(WF, na))).items():
                saisho = next((i for i, d in enumerate(dans) if mon_wo_hajimeru(d)), None)
                if saisho is None:
                    continue
                mitsuketa += 1
                mae = dans[:saisho]
                with self.subTest(workflow=na, job=job):
                    dasu = [d for d in mae if yoyaku_wo_dasu(d)]
                    self.assertTrue(dasu, "予約台帳を _yoyaku に出す段が、門を始める段より前に無い")
                    for d in dasu:
                        self.assertNotIn("continue-on-error", d, "予約台帳を出せなかった回を、先へ進めてしまう")
                    self.assertTrue(any(yoyaku_wo_watasu(d) for d in mae),
                                    "YOYAKU_DIR・YOYAKU_REPO を、門を始める段より前に渡していない")
                    self.assertTrue(any(hajime_wo_kaku(d) for d in mae),
                                    "RUN_HAJIME を、門を始める段より前に書いていない")
                    for d in dans:
                        if re.search(r"RUN_DATE=.*GITHUB_ENV", d):
                            self.assertIn("RUN_HAJIME=", d, "RUN_DATE だけを書き直す段がある（開始時刻とずれる）")
        # **1つも見つからないなら、見張りが黙っている**（取りに行く workflow はどの置き場にもある）
        self.assertGreater(mitsuketa, 0, "門を始める workflow が1つも見つからない")

    def test_gitignoreが予約台帳の置き場を外している(self):
        gyou = [l.strip() for l in _yomu(os.path.join(HERE, ".gitignore")).splitlines()]
        self.assertTrue({"_yoyaku/", "/_yoyaku/", "_yoyaku", "/_yoyaku"} & set(gyou))

    def test_相手台帳が予約を要ると書き_どの相手にも固定IDがある(self):
        d = json.loads(_yomu(os.path.join(HERE, kado.DAICHO)))
        self.assertEqual(d.get("yoyaku"), {"hitsuyou": True, "repo": YOYAKU_REPO})
        ids = {}
        for aite, a in d["aite"].items():
            with self.subTest(aite=aite):
                self.assertRegex(a.get("id") or "", kado.YOYAKU_ID)
                self.assertNotIn(a["id"], ids, "相手IDが重なっている（%s）" % ids.get(a["id"]))
                ids[a["id"]] = aite
        self.assertTrue(ids)

    def test_門は本番の置き場に書き_4つそろって同じ実行とみなす(self):
        self.assertEqual(kado.YOYAKU_MICHI, "yoyaku")
        self.assertEqual(kado.YOYAKU_NINSHIKI,
                         ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB"))


if __name__ == "__main__":
    unittest.main()
