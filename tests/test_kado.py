"""取りに行く前の門（common/kado.py）と、完全観測（common/kanzen.py）の検査。

**本物の相手には1本も出さない。** 通信は偽の相手（Nise）が受ける。
承認の出どころは、一時フォルダに作った git の置き場で確かめる。

    python3 -m unittest tests.test_kado
"""
import contextlib
import datetime
import email.message
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
import urllib.response

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from common import kado, kanzen  # noqa: E402

UA = "kujiraya archive bot (+https://example.invalid/about; https://example.invalid/form)"
REPO = "tameshi-repo"
HOST = "www.example.lg.jp"
URL = "https://%s/data/list.html" % HOST


def jikoku(hi="2026-09-25", ji="07:00:00"):
    """日本時間のその時刻（epoch 秒）。偽の時計に使う。"""
    return datetime.datetime.fromisoformat("%sT%s+09:00" % (hi, ji)).timestamp()


# 偽の時計の基準（この検査の RUN_DATE 2026-09-25 の朝7時）。**門は外へ出す前に、いまの日付を見る**
ASA = jikoku()


class Nise(urllib.request.BaseHandler):
    """偽の相手。URL ごとに (status, headers, body) を返し、来た URL を全部控える。"""
    handler_order = 50

    def __init__(self, kotae):
        self.kotae = kotae
        self.kita = []
        self.nanori = []           # 来た要求の名乗り（User-Agent）
        self.naka = []             # 来た要求の (メソッド, 中身, 見出し)

    def _open(self, req):
        self.kita.append(req.full_url)
        self.naka.append((req.get_method(), req.data, dict(req.header_items())))
        self.nanori.append(req.get_header("User-agent") or req.unredirected_hdrs.get("User-agent"))
        kotae = self.kotae.get(req.full_url, (404, {}, b""))
        status, headers, body = kotae[:3]
        saki = kotae[3] if len(kotae) > 3 else req.full_url     # 相手の側で行き先が変わった形を作るとき
        if isinstance(status, Exception):
            raise status
        msg = email.message.Message()
        for k, v in headers.items():
            msg[k] = v
        r = urllib.response.addinfourl(io.BytesIO(body), msg, saki, status)
        r.msg = "nise"
        return r

    https_open = _open
    http_open = _open


ROBOTS_OK = (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /himitsu/\n")
HONBUN = (200, {"Content-Type": "text/html"}, b"<html>ok</html>")


def yoi_card(**kae):
    c = {
        "取得元": "ためしの一覧", "相手": "ためし県", "source種別": "行政",
        "対象URL": URL, "対象host": [HOST],
        "個人情報を含みうる": "分からない", "当事者に個人がありうる": "分からない",
        "個票の粒度": "集計のみ（個票なし）", "所在地の扱い": "個人の所在地は無い",
        "氏名を含みうる": "いいえ", "個人の電話番号を含みうる": "いいえ",
        "個人の生活住所を含みうる": "いいえ",
        "privateに保存する予定": "取得したページそのまま", "publicに出す予定": "件数の集計だけ",
        "公開時の粒度": "市区町村・月ごとの件数",
        "規約確認日": "2026-09-20",
        "規約証跡": {"規約URL": "https://%s/kiyaku.html" % HOST},
        "正規提供手段": "無し", "正規提供手段の理由": "API も CSV も無い",
        "承認対象の行為": ["自動取得", "内部保存", "差分記録"],
        "承認する取得方法": {"URL範囲": ["https://%s/data/" % HOST], "対象種類": "HTML",
                         "ページ送り・深さ": "1段", "API/feed": "なし", "承認頻度": "1日1回"},
        "重要な原文": "「このサイトの情報は、出典を記載すれば自由に利用できます」",
        "統括判定案": "取ってよい", "判定理由": "利用条件が複製・加工・商用を明示的に許している",
        "肯定根拠番号": "1", "不確定事項": "なし", "専門家確認": "不要",
        "再確認期限": "2026-12-31", "再確認理由": "年1回の規約改定に合わせる",
        # 正本 3.4a（2026-09-29）：route と出どころ・商品用の継続観測の可否
        "route種別": "web", "source_id": "tameshi-ken", "商品用継続観測の可否": "可",
        "このrouteのデータを商品に使えるか": "使える",
        "カード版": 1,
    }
    c.update(kae)
    return c


def git(root, *args, env=None):
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "運営者", "GIT_AUTHOR_EMAIL": "unei@example.invalid",
              "GIT_COMMITTER_NAME": "運営者", "GIT_COMMITTER_EMAIL": "unei@example.invalid"})
    e.update(env or {})
    subprocess.run(["git", "-C", root] + list(args), check=True, capture_output=True, env=e)


class Oki(unittest.TestCase):
    """一時フォルダに、カード・承認・相手台帳・金庫を置く。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.kinko = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "data", "ref", "shounin"))
        self.cards = {"tameshi": yoi_card()}
        # 予約台帳は下の「予約台帳」で試す。ここでは要らないと書く（書かなければ、要る側に倒れる）
        self.daicho = {"yoyaku": {"hitsuyou": False}, "aite": {"ためし県": {"host": [HOST], "担当": REPO}}}
        self.env = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1", "RUN_DATE": "2026-09-25"}
        self.kaku_all()
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "はじめ")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.kinko, ignore_errors=True)
        urllib.request.install_opener(None)

    def p(self, *a):
        return os.path.join(self.root, *a)

    def kaku(self, rel, d):
        with open(self.p(rel), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def kaku_all(self):
        self.kaku("data/ref/torimoto-card.json", {"cards": self.cards})
        self.kaku("data/ref/aite-daicho.json", self.daicho)

    def shounin(self, cid="tameshi", ai=False, card=None, env=None, **kae):
        card = card or self.cards[cid]
        s = {"カード": cid, "運営者承認": "承認", "承認したカード版": card["カード版"],
             "承認時カード指紋": kado.card_shimon(card), "承認日": "2026-09-24",
             "approved_by": "operator", "entered_by": "operator"}
        s.update(kae)
        s = {k: v for k, v in s.items() if v is not None}
        self.kaku("data/ref/shounin/%s.json" % cid, s)
        git(self.root, "add", "-A")
        msg = "承認"
        if ai:
            msg += "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
        git(self.root, "commit", "-q", "-m", msg, env=env)

    def mon(self, kotae=None, **kw):
        kw.setdefault("kansoku_demoto", True)
        self.nise = Nise(kotae if kotae is not None else {
            "https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN})
        self.naps = []
        return kado.Kado(self.root, REPO, UA, env=self.env, transport=self.nise,
                         run_id=kw.pop("run_id", "run-1"), sleep=self.naps.append,
                         now=kw.pop("now", lambda: ASA + 1000.0), **kw)

    def toru(self, k, cid="tameshi", url=URL):
        k.install()
        with k.sesshon(cid, hozon_saki=os.path.join(self.kinko, "raw")):
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.read()


class 規約の門(Oki):

    def test_取ってよい_承認がそろえば通る(self):
        self.shounin()
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "取ってよい")
        self.assertEqual(self.toru(k), b"<html>ok</html>")
        self.assertEqual(self.nise.kita, ["https://%s/robots.txt" % HOST, URL])

    def test_未確認は通らない_通信も出ない(self):
        self.cards["tameshi"] = yoi_card(統括判定案="")
        self.kaku_all()
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "未確認")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_カードが無ければ未確認で通らない(self):
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.toru(k, cid="nai")
        self.assertEqual(self.nise.kita, [])

    def test_規約未確定は通らない(self):
        self.cards["tameshi"] = yoi_card(統括判定案="規約未確定")
        self.kaku_all()
        self.shounin()
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "規約未確定")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_取ってはいけないは通らない(self):
        self.cards["tameshi"] = yoi_card(統括判定案="取ってはいけない")
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        self.assertEqual(self.nise.kita, [])

    def test_移行元の取ってはいけない_規約未確定を未確認へ戻さない(self):
        for w in ("取ってはいけない", "規約未確定"):
            self.assertEqual(kado.seishiki_jotai(
                yoi_card(統括判定案="", 移行元={"旧値": w}), False), w)
        self.assertEqual(kado.seishiki_jotai(
            yoi_card(統括判定案="", 移行元={"旧値": "取ってよい"}), False), "未確認")

    def test_承認が無ければ規約未確定(self):
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "規約未確定")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)

    def test_カード版が違えば通らない(self):
        self.shounin(承認したカード版=0)
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        self.assertEqual(self.nise.kita, [])

    def test_承認のあとでカードが変わったら通らない(self):
        self.shounin()
        self.cards["tameshi"]["重要な原文"] = "「書き換えた」"
        self.kaku_all()
        git(self.root, "commit", "-qam", "カードを直した")
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "規約未確定")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_保存していない承認は数えない(self):
        self.shounin()
        s = json.load(open(self.p("data/ref/shounin/tameshi.json"), encoding="utf-8"))
        s["承認日"] = "2026-09-25"
        self.kaku("data/ref/shounin/tameshi.json", s)
        self.assertFalse(self.mon().shounin("tameshi")[0])

    def test_個人情報と個票の取得前ゲートが未決なら取ってよいにならない(self):
        """正本 3.1・3.4a。9つのどれかが未決なら、取得を始めない。"""
        kaketa = {"当事者に個人がありうる": "", "氏名を含みうる": "未確認",
                  "個人の電話番号を含みうる": None, "個人の生活住所を含みうる": "たぶん無い",
                  "個票の粒度": "未確認", "所在地の扱い": "", "privateに保存する予定": "未決",
                  "publicに出す予定": "分からない", "公開時の粒度": ""}
        for k, v in kaketa.items():
            with self.subTest(k=k):
                c = yoi_card(**{k: v})
                self.assertIn(k, "、".join(kado._kaketeiru(c)))
                self.assertEqual(kado.seishiki_jotai(c, True), "規約未確定")
        # 「分からない」は、含みうる側として決めたことになる（きつい側に扱う）
        self.assertEqual(kado.seishiki_jotai(yoi_card(氏名を含みうる="分からない"), True), "取ってよい")

    def test_肯定根拠4は行政と準公的だけ(self):
        """4（行政等の公式な再利用条件）は民間には使えない（正本 3.4a）。"""
        for sh in ("民間一次", "二次・集約"):
            with self.subTest(sh=sh):
                self.assertEqual(kado.seishiki_jotai(
                    yoi_card(肯定根拠番号="4", **{"source種別": sh}), True), "規約未確定")
        for sh in ("行政", "準公的"):
            self.assertEqual(kado.seishiki_jotai(
                yoi_card(肯定根拠番号="4", **{"source種別": sh}), True), "取ってよい")
        for n in ("1", "2", "3"):
            self.assertEqual(kado.seishiki_jotai(
                yoi_card(肯定根拠番号=n, **{"source種別": "民間一次"}), True), "取ってよい")
        # 5 は STOP 条件を確かめた記録が無ければ取ってよいにならない（正本 3.4a。下の検査）
        self.assertEqual(kado.seishiki_jotai(yoi_card(肯定根拠番号="5"), True), "規約未確定")
        self.assertEqual(kado.seishiki_jotai(yoi_card(肯定根拠番号="6"), True), "規約未確定")

    def test_該当文言なしだけでは取ってよいにならない(self):
        self.cards["tameshi"] = yoi_card(判定理由="該当文言なし")
        self.kaku_all()
        self.shounin()
        self.assertEqual(self.mon().seishiki("tameshi"), "規約未確定")

    def test_取得開始前の専門家確認が未了なら通らない(self):
        self.cards["tameshi"] = yoi_card(専門家確認="取得開始前に必要")
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_二次集約は正規提供手段を採用していなければ通らない(self):
        self.cards["tameshi"] = yoi_card(**{"source種別": "二次・集約"})
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_正規提供手段が未調査なら通らない(self):
        self.cards["tameshi"] = yoi_card(正規提供手段="未調査")
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_承認されていない行為は通らない(self):
        self.cards["tameshi"] = yoi_card(承認対象の行為=["自動取得"])
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_道すじのドットで範囲をすり抜けない(self):
        self.shounin()
        for u in ("https://%s/data/../himitsu/x.html" % HOST,
                  "https://%s/data/%%2e%%2e/himitsu/x.html" % HOST,
                  "https://%s/data/./x.html" % HOST):
            with self.subTest(u=u):
                with self.assertRaises(kado.Tomeru):
                    self.toru(self.mon(run_id="run-dot"), url=u)
                self.assertNotIn(u, self.nise.kita)

    def test_ftpは通さない(self):
        self.shounin()
        k = self.mon().install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            with self.assertRaises(urllib.error.URLError):
                urllib.request.urlopen("ftp://%s/data/x.txt" % HOST, timeout=5)
        urllib.request.install_opener(urllib.request.build_opener(kado._Tozasu()))
        with self.assertRaises(kado.Tomeru):
            urllib.request.urlopen("ftp://example.invalid/x", timeout=1)

    def test_URL範囲の外は通らない(self):
        self.shounin()
        other = "https://%s/himitsu2/x.html" % HOST
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(), url=other)
        self.assertNotIn(other, self.nise.kita)


class 承認した者と書き込んだ者(Oki):
    """承認できるのは運営者だけ。運営者が承認した中身は、だれが転記してもよい（2026-09-28）。
    **commit した者から、承認した者を推し量らない。**"""

    def kazoeru(self, **kae):
        self.shounin(**kae)
        return self.mon().shounin("tameshi")

    def test_1_運営者が承認して運営者が書いた(self):
        self.assertEqual(self.kazoeru(), (True, ""))

    def test_2_運営者が承認してCodexが転記した(self):
        self.assertEqual(self.kazoeru(entered_by="codex", env={
            "GIT_AUTHOR_NAME": "codex", "GIT_AUTHOR_EMAIL": "codex@example.invalid"}), (True, ""))

    def test_3_運営者が承認してClaudeが転記した_AIの印があっても数える(self):
        self.assertEqual(self.kazoeru(entered_by="claude", ai=True), (True, ""))

    def test_4_AI自身を承認した者として書いた承認は数えない(self):
        for sha in ("claude", "codex", "ai", "pc-sanbo", "統括", "github-actions"):
            with self.subTest(approved_by=sha):
                self.setUp()
                ok, why = self.kazoeru(approved_by=sha, entered_by=sha)
                self.assertFalse(ok)
                self.assertIn("運営者だけ", why)

    def test_承認した者と書き込んだ者のどちらかが無ければ数えない(self):
        for kae in ({"approved_by": None}, {"entered_by": None}, {"entered_by": " "}):
            with self.subTest(kae=kae):
                self.setUp()
                self.assertFalse(self.kazoeru(**kae)[0])

    def test_印の無いcommitでも_承認した者が運営者でなければ数えない(self):
        # 前の門は「AI の印が無い commit ＝運営者」と推し量っていた。いまは中身の approved_by で読む
        self.assertFalse(self.kazoeru(approved_by="codex", entered_by="codex")[0])

    def test_改名しても承認した者の読み方は変わらない(self):
        # 前の門は、AI の印の commit で入れたファイルを印の無い commit で改名すると通った
        self.shounin(approved_by="claude", entered_by="claude", ai=True)
        git(self.root, "mv", "data/ref/shounin/tameshi.json", "data/ref/shounin/tameshi2.json")
        git(self.root, "commit", "-qm", "改名")
        git(self.root, "mv", "data/ref/shounin/tameshi2.json", "data/ref/shounin/tameshi.json")
        git(self.root, "commit", "-qm", "戻した")
        self.assertFalse(self.mon().shounin("tameshi")[0])

    def test_自動実行が書き換えても_保存された中身で読む(self):
        self.shounin()
        with open(self.p("data/ref/shounin/tameshi.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        git(self.root, "commit", "-qam", "自動",
            env={"GIT_AUTHOR_NAME": "github-actions[bot]",
                 "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"})
        self.assertEqual(self.mon().shounin("tameshi"), (True, ""))


class 機械札(Oki):

    def test_札があれば通らない(self):
        self.shounin()
        for f in ("再読要", "停止要請", "使用停止", "不整合", "混雑継続",
                  "拒否継続", "引用不一致", "指紋ゆらぎ"):
            with self.subTest(f=f):
                self.kaku("data/ref/kikai-fuda.json", {"tameshi": {"札": f}})
                k = self.mon()
                with self.assertRaises(kado.Tomeru):
                    self.toru(k)
                self.assertEqual(self.nise.kita, [])

    def test_知らない札も通らない(self):
        self.shounin()
        self.kaku("data/ref/kikai-fuda.json", {"tameshi": {"札": "たぶん大丈夫"}})
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_再確認期限を過ぎたら再読要を付けて止める_4語は書き換えない(self):
        self.cards["tameshi"] = yoi_card(再確認期限="2026-09-01")
        self.kaku_all()
        self.shounin()
        mae = open(self.p("data/ref/torimoto-card.json"), "rb").read()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        f = json.load(open(self.p("data/ref/kikai-fuda.json"), encoding="utf-8"))
        self.assertEqual(f["tameshi"]["札"], "再読要")
        self.assertEqual(open(self.p("data/ref/torimoto-card.json"), "rb").read(), mae)

    def test_機械は札を外せない_4語も付けられない(self):
        k = self.mon()
        for f in ("なし", "取ってよい", "未確認"):
            with self.assertRaises(ValueError):
                k.fuda_wo_tsukeru("tameshi", f, "")


class 相手台帳と頻度(Oki):

    def test_担当でない相手には行かない(self):
        self.daicho["aite"]["ためし県"]["担当"] = "ほかの置き場"
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        self.assertEqual(self.nise.kita, [])

    def test_カードの門だけでも担当を見る(self):
        """URL ごとの関所より前に、カードの門で止まること（通信の前に止める段）。"""
        self.daicho["aite"]["ためし県"]["担当"] = "ほかの置き場"
        self.kaku_all()
        self.shounin()
        riyuu = self.mon().card_mon("tameshi", hozon_saki=os.path.join(self.kinko, "raw"))
        self.assertTrue(any("担当" in r for r in riyuu), riyuu)

    def test_カードの門だけでも今日ほかの回が見た相手を止める(self):
        self.shounin()
        self.toru(self.mon(run_id="run-1"))
        riyuu = self.mon(run_id="run-2").card_mon("tameshi", hozon_saki=os.path.join(self.kinko, "raw"))
        self.assertTrue(any("今日もう見た" in r for r in riyuu), riyuu)

    def test_担当が未定の相手には行かない(self):
        self.daicho["aite"]["ためし県"]["担当"] = "未定"
        self.kaku_all()
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())

    def test_今日ほかの回が見た相手には行かない(self):
        self.shounin()
        self.toru(self.mon(run_id="run-1"))
        k2 = self.mon(run_id="run-2")
        with self.assertRaises(kado.Tomeru):
            self.toru(k2)
        self.assertEqual(self.nise.kita, [])

    def test_同じ回の別の処理は続けて見られる_5秒あける(self):
        self.shounin()
        self.toru(self.mon(run_id="run-1", now=lambda: ASA + 1000.0))
        k2 = self.mon(run_id="run-1", now=lambda: ASA + 1001.0)
        self.toru(k2)
        self.assertTrue(self.naps and max(self.naps) >= 4.0)

    def test_429のあと同じ相手へ続けない(self):
        self.shounin()
        u2 = "https://%s/data/two.html" % HOST
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK,
                      URL: (429, {"Retry-After": "60"}, b""), u2: HONBUN})
        k.install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(URL, timeout=5)
            with self.assertRaises(kado.Tomeru):
                urllib.request.urlopen(u2, timeout=5)
        self.assertNotIn(u2, self.nise.kita)
        # 段を1つずつ見る。**この処理の中の控えだけでも止まる**（控えのファイルが無くても）
        self.assertIn("ためし県", k._tomatta)
        os.remove(self.p("data/ref/aite-kyou.json"))
        k._genzai = ("tameshi", self.cards["tameshi"], kado.KOUI_TORU)
        try:
            with self.assertRaises(kado.Tomeru):
                k.url_mon(u2)
        finally:
            k._genzai = None
        # **今日の控え（ファイル）だけでも止まる**。同じ回の別の処理（別の .py）
        self.kaku("data/ref/aite-kyou.json",
                  {"ためし県": {"hi": "2026-09-25", "run": "run-1", "tomatta": "HTTP 429"}})
        k2 = self.mon(run_id="run-1")
        with self.assertRaises(kado.Tomeru):
            self.toru(k2, url=u2)
        self.assertEqual(self.nise.kita, [])

    def test_セッションの途中でほかの処理が止めた相手には出さない(self):
        """URL ごとの関所も、今日の控えを見る（カードの門を通ったあとで、別の処理が 503 を書いた形）。"""
        self.shounin()
        k = self.mon()
        k._genzai = ("tameshi", self.cards["tameshi"], kado.KOUI_TORU)
        self.kaku("data/ref/aite-kyou.json",
                  {"ためし県": {"hi": "2026-09-25", "run": "run-1", "tomatta": "HTTP 503"}})
        try:
            with self.assertRaises(kado.Tomeru):
                k.url_mon(URL)
        finally:
            k._genzai = None
        self.assertEqual(self.nise.kita, [])

    def test_401_403でも押し込まない(self):
        self.shounin()
        for code in (401, 403):
            with self.subTest(code=code):
                shutil.rmtree(self.p("data", "ref", "aite-kyou.json"), ignore_errors=True)
                if os.path.exists(self.p("data/ref/aite-kyou.json")):
                    os.remove(self.p("data/ref/aite-kyou.json"))
                u2 = "https://%s/data/two.html" % HOST
                k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK,
                              URL: (code, {}, b""), u2: HONBUN}, run_id="run-%d" % code)
                k.install()
                with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(URL, timeout=5)
                    with self.assertRaises(kado.Tomeru):
                        urllib.request.urlopen(u2, timeout=5)
                self.assertNotIn(u2, self.nise.kita)

    def test_門を通っていない通信は出ない(self):
        self.shounin()
        k = self.mon().install()
        with self.assertRaises(kado.Tomeru):
            urllib.request.urlopen(URL, timeout=5)
        self.assertEqual(self.nise.kita, [])
        self.assertIs(kado.genzai(), k)


class 金庫(Oki):

    def test_金庫が無ければ本体もrobotsも取りに行かない(self):
        self.shounin()
        for env in ({}, {"KINKO_DIR": self.kinko}, {"KINKO_DIR": "/nai/nai", "KINKO_PRIVATE": "1"}):
            with self.subTest(env=env):
                self.env = dict(env, RUN_DATE="2026-09-25")
                with self.assertRaises(kado.Tomeru):
                    self.toru(self.mon())
                self.assertEqual(self.nise.kita, [])

    def test_保存先が金庫の外なら取りに行かない(self):
        self.shounin()
        k = self.mon().install()
        with self.assertRaises(kado.Tomeru):
            with k.sesshon("tameshi", hozon_saki=self.p("data", "raw")):
                urllib.request.urlopen(URL, timeout=5)
        self.assertEqual(self.nise.kita, [])


class robotsの応答(Oki):

    def kiku(self, robots):
        self.shounin()
        k = self.mon({"https://%s/robots.txt" % HOST: robots, URL: HONBUN})
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertNotIn(URL, self.nise.kita)

    def test_401_403_5xx_は通らない(self):
        # 中身は robots.txt として読める形にしておく。**番号だけで止まっていること**を見るため
        yomeru = b"User-agent: *\nAllow: /\n"
        for code in (401, 403, 500, 502, 504, 451, 400, 302, 299 + 1, 100):
            with self.subTest(code=code):
                self.setUp()
                self.kiku((code, {"Content-Type": "text/plain"}, yomeru))
        for code in (401, 403, 500, 451, 302):
            st, _, _ = kado.robots_hantei(code, "text/plain", yomeru)
            self.assertEqual(st, "確かめられなかった", code)

    def test_届かないは通らない(self):
        self.kiku((urllib.error.URLError("timeout"), {}, b""))

    def test_HTMLは通らない(self):
        # text/plain と名乗っていても、中身が HTML なら通さない。**規則らしい行が混ざっていても**
        mazari = b"<!DOCTYPE html>\n<html>\nUser-agent: *\nAllow: /\n</html>\n"
        self.assertEqual(kado.robots_hantei(200, "text/plain", mazari)[0], "確かめられなかった")
        self.assertEqual(kado.robots_hantei(200, "", b"<html>\nUser-agent: *\nAllow: /\n")[0],
                         "確かめられなかった")
        self.kiku((200, {"Content-Type": "text/plain"}, mazari))
        self.setUp()
        self.kiku((200, {"Content-Type": "text/html"}, b"<html><body>top</body></html>"))
        self.setUp()
        self.kiku((200, {"Content-Type": "text/plain"}, b"<!DOCTYPE html><html></html>"))
        self.setUp()
        self.kiku((200, {}, b"  <html>"))

    def test_空と読めない中身は通らない(self):
        self.kiku((200, {"Content-Type": "text/plain"}, b""))
        self.setUp()
        self.kiku((200, {"Content-Type": "text/plain"}, b"\x89PNG\r\n\x1a\nzzzz"))
        self.setUp()
        self.kiku((200, {"Content-Type": "text/plain"}, "ようこそ\nこれは説明です\n".encode("utf-8")))
        self.setUp()
        self.kiku((200, {"Content-Type": "application/octet-stream"}, b"User-agent: *\nAllow: /\n"))

    def test_404_410は通す(self):
        for code in (404, 410):
            with self.subTest(code=code):
                self.setUp()
                self.shounin()
                k = self.mon({"https://%s/robots.txt" % HOST: (code, {}, b""), URL: HONBUN})
                self.assertEqual(self.toru(k), b"<html>ok</html>")

    def test_429_503は混んでいる_その相手は止まる(self):
        for code in (429, 503):
            with self.subTest(code=code):
                self.setUp()
                self.kiku((code, {}, b""))

    def test_Disallowなら取らず拒否継続を付ける(self):
        self.kiku((200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /data/\n"))
        f = json.load(open(self.p("data/ref/kikai-fuda.json"), encoding="utf-8"))
        self.assertEqual(f["tameshi"]["札"], "拒否継続")

    def test_転送先のpathも照合する(self):
        self.shounin()
        saki = "https://%s/himitsu/x.html" % HOST
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK,
                      URL: (302, {"Location": saki}, b""), saki: HONBUN})
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertNotIn(saki, self.nise.kita)

    def test_転送先が別の相手なら行かない(self):
        self.shounin()
        saki = "https://other.example.com/data/x.html"
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK,
                      URL: (301, {"Location": saki}, b""), saki: HONBUN})
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertNotIn(saki, self.nise.kita)
        self.assertNotIn("https://other.example.com/robots.txt", self.nise.kita)

    def test_robots_txt_のよその場所への転送は辿らない(self):
        self.kiku((301, {"Location": "https://%s/robots2.txt" % HOST}, b""))
        self.assertNotIn("https://%s/robots2.txt" % HOST, self.nise.kita)
        self.setUp()
        self.kiku((302, {"Location": "https://%s/top.html" % HOST}, b""))
        self.assertNotIn("https://%s/top.html" % HOST, self.nise.kita)
        self.setUp()
        self.kiku((301, {"Location": "https://other.example.com/robots.txt"}, b""))
        self.assertNotIn("https://other.example.com/robots.txt", self.nise.kita)

    def test_答えがよその場所から返ったら読まない(self):
        """転送の段を通らずに、答えの出どころが /robots.txt でなかった形（二重の守り）。"""
        k = self.mon({"https://%s/robots.txt" % HOST:
                      (200, {"Content-Type": "text/plain"}, b"User-agent: *\nAllow: /\n",
                       "https://%s/top.html" % HOST)})
        self.assertEqual(k._robots_toru("https", HOST, "ためし県")[0], "確かめられなかった")

    def test_同じ相手のhttpからhttpsへの転送は辿って読む(self):
        k = self.mon({"http://%s/robots.txt" % HOST: (301, {"Location": "https://%s/robots.txt" % HOST}, b""),
                      "https://%s/robots.txt" % HOST: ROBOTS_OK})
        jotai, rules, _, _ = k._robots_toru("http", HOST, "ためし県")
        self.assertEqual(jotai, "通す")
        self.assertFalse(kado.robots_yurusu(rules, "http://%s/himitsu/a" % HOST))
        self.assertEqual(self.nise.kita, ["http://%s/robots.txt" % HOST, "https://%s/robots.txt" % HOST])

    def test_同じ相手のhttpからhttpsの404は置いていないとして通す(self):
        k = self.mon({"http://%s/robots.txt" % HOST: (301, {"Location": "https://%s/robots.txt" % HOST}, b""),
                      "https://%s/robots.txt" % HOST: (404, {}, b"")})
        self.assertEqual(k._robots_toru("http", HOST, "ためし県")[0], "通す")

    def test_見出しがtext_htmlでも中身が規則なら読む(self):
        st, groups, _ = kado.robots_hantei(200, "text/html; charset=utf-8", b"User-agent: *\nDisallow: /x\n")
        self.assertEqual(st, "通す")
        self.assertEqual(kado.robots_hantei(200, "text/html", b"<html><body>404</body></html>")[0],
                         "確かめられなかった")

    def test_圧縮されたままなら読まない(self):
        self.assertEqual(kado.robots_hantei(200, "text/plain", b"User-agent: *\nAllow: /\n", "gzip")[0],
                         "確かめられなかった")
        self.assertEqual(kado.robots_hantei(200, "text/plain", b"User-agent: *\nAllow: /\n", "identity")[0],
                         "通す")

    def test_robots_txtは1回の実行で相手ごとに1回だけ_名乗って取る(self):
        self.shounin()
        u2 = "https://%s/data/two.html" % HOST
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN, u2: HONBUN})
        k.install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            urllib.request.urlopen(URL, timeout=5).read()
            urllib.request.urlopen(u2, timeout=5).read()
        self.assertEqual(self.nise.kita.count("https://%s/robots.txt" % HOST), 1)
        self.assertEqual(self.nise.nanori[0], UA)


class robotsのバイト(Oki):
    """**文字コードを選ばない。** 注記（# から後ろ）はバイトのまま落とし、規則の行だけを読む。"""

    def test_ShiftJISの注記があっても規則は読める(self):
        生 = "# 競売情報サイトのご案内\r\nUser-agent: *\r\nDisallow: /himitsu/ # 注記\r\n".encode("cp932")
        st, groups, _ = kado.robots_hantei(200, "text/plain", 生)
        self.assertEqual(st, "通す")
        rules, _ = kado.robots_group(groups, {"kujiraya"})
        self.assertFalse(kado.robots_yurusu(rules, "https://h.example/himitsu/a"))
        self.assertTrue(kado.robots_yurusu(rules, "https://h.example/data/a"))

    def test_規則の行にASCIIの外の字があれば止める(self):
        for 生 in ("User-agent: *\nDisallow: /日本/\n".encode("cp932"),
                   "User-agent: *\nDisallow: /日本/\n".encode("utf-8")):
            with self.subTest(生=生):
                self.assertEqual(kado.robots_hantei(200, "text/plain", 生)[0], "確かめられなかった")

    def test_控えはバイトのまま金庫の中に残す(self):
        self.shounin()
        生 = "# 案内\nUser-agent: *\nDisallow: /himitsu/\n".encode("cp932")
        hikae = os.path.join(self.kinko, "raw", "_robots")
        self.nise = Nise({"https://%s/robots.txt" % HOST: (200, {"Content-Type": "text/plain"}, 生),
                          URL: HONBUN})
        k = kado.Kado(self.root, REPO, UA, kansoku_demoto=True, env=self.env, transport=self.nise, run_id="run-h",
                      sleep=lambda s: None, now=lambda: ASA + 1000.0, robots_hikae=hikae)
        k.install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            urllib.request.urlopen(URL, timeout=5).read()
        with open(os.path.join(hikae, HOST + ".txt"), "rb") as f:
            oita = f.read()
        # 頭の2行（取得日・出どころ）は控え。そのあとは受け取ったバイトそのまま
        atama = ("# 取得日: 2026-09-25\n# https://%s/robots.txt\n" % HOST).encode("utf-8")
        self.assertEqual(oita, atama + 生)

    def test_控えの置き場が金庫の外なら書かない(self):
        self.shounin()
        soto = os.path.join(self.root, "data", "raw", "_robots")
        k = kado.Kado(self.root, REPO, UA, kansoku_demoto=True, env=self.env, transport=Nise({
            "https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN}), run_id="run-s",
            sleep=lambda s: None, now=lambda: ASA + 1000.0, robots_hikae=soto)
        k.install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            urllib.request.urlopen(URL, timeout=5).read()
        self.assertFalse(os.path.exists(os.path.join(soto, HOST + ".txt")))


class 日付(Oki):
    """**門は時計を見ない。** 日付は置き場の「日付を決める1か所」から渡してもらう。"""

    def test_日付が渡されていなければ止める_通信も出ない(self):
        self.shounin()
        self.env = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1"}
        k = self.mon()
        self.assertIsNone(k.today)
        self.assertTrue(any("日付" in r for r in k.card_mon("tameshi")))
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_読めないRUN_DATEは今日に倒さず落とす(self):
        for bad in ("きのう", "2026/09/25", "20260925", "2026-13-99"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    kado.Kado(self.root, REPO, UA, env={"RUN_DATE": bad})
        with self.assertRaises(ValueError):
            kado.Kado(self.root, REPO, UA, env={}, today="あした")

    def test_渡された日を使う(self):
        self.shounin()
        self.env = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1"}
        k = self.mon(today="2026-09-25")
        self.assertEqual(k.today, "2026-09-25")
        self.assertEqual(self.toru(k), b"<html>ok</html>")


class robots照合器(unittest.TestCase):
    """RFC 9309。Python 標準の robotparser は使っていない。"""

    def yurusu(self, text, path, tokens=("kujiraya archive bot", "kujiraya")):
        groups, _ = kado.robots_kaiseki(text)
        rules, _ = kado.robots_group(groups, tokens)
        return kado.robots_yurusu(rules, "https://h.example" + path)

    def test_最長一致(self):
        t = "User-agent: *\nDisallow: /a/\nAllow: /a/b/\n"
        self.assertTrue(self.yurusu(t, "/a/b/c"))
        self.assertFalse(self.yurusu(t, "/a/c"))
        t2 = "User-agent: *\nAllow: /a/\nDisallow: /a/b/\n"
        self.assertFalse(self.yurusu(t2, "/a/b/c"))

    def test_同じ長さならAllow(self):
        t = "User-agent: *\nDisallow: /page\nAllow: /page\n"
        self.assertTrue(self.yurusu(t, "/page"))

    def test_星(self):
        t = "User-agent: *\nDisallow: /*.pdf\n"
        self.assertFalse(self.yurusu(t, "/x/y.pdf"))
        self.assertFalse(self.yurusu(t, "/x/y.pdf?v=1"))
        self.assertTrue(self.yurusu(t, "/x/y.html"))

    def test_ドル(self):
        t = "User-agent: *\nDisallow: /*.pdf$\n"
        self.assertFalse(self.yurusu(t, "/y.pdf"))
        self.assertTrue(self.yurusu(t, "/y.pdf?v=1"))
        t2 = "User-agent: *\nDisallow: /$\n"
        self.assertFalse(self.yurusu(t2, "/"))
        self.assertTrue(self.yurusu(t2, "/a"))

    def test_BOMで規則が消えない(self):
        t = "﻿User-agent: *\nDisallow: /\n"
        self.assertFalse(self.yurusu(t, "/a"))
        st, groups, _ = kado.robots_hantei(200, "text/plain", t.encode("utf-8"))
        self.assertEqual(st, "通す")
        rules, _ = kado.robots_group(groups, {"kujiraya"})
        self.assertFalse(kado.robots_yurusu(rules, "https://h.example/a"))

    def test_空のDisallowは何も止めない(self):
        t = "User-agent: *\nDisallow:\n"
        self.assertTrue(self.yurusu(t, "/a"))

    def test_名指しのグループが先(self):
        t = "User-agent: *\nDisallow: /\n\nUser-agent: kujiraya\nAllow: /\n"
        self.assertTrue(self.yurusu(t, "/a"))
        t2 = "User-agent: *\nAllow: /\n\nUser-agent: Kujiraya\nDisallow: /x\n"
        self.assertFalse(self.yurusu(t2, "/x/1"))
        self.assertTrue(self.yurusu(t2, "/y"))

    def test_複数のuser_agent行で1グループ(self):
        t = "User-agent: a\nUser-agent: kujiraya\nDisallow: /z\n"
        self.assertFalse(self.yurusu(t, "/z"))

    def test_パーセント符号化をそろえる(self):
        t = "User-agent: *\nDisallow: /%7Ejoe/\n"
        self.assertFalse(self.yurusu(t, "/~joe/index.html"))
        t2 = "User-agent: *\nDisallow: /日本/\n"
        self.assertFalse(self.yurusu(t2, "/%E6%97%A5%E6%9C%AC/a"))

    def test_robots_txt自身はいつも許す(self):
        self.assertTrue(self.yurusu("User-agent: *\nDisallow: /\n", "/robots.txt"))

    def test_Crawl_delayの長いほう(self):
        groups, _ = kado.robots_kaiseki("User-agent: *\nCrawl-delay: 12\nDisallow: /x\n")
        _, delay = kado.robots_group(groups, {"kujiraya"})
        self.assertEqual(delay, 12.0)


class 完全観測(unittest.TestCase):

    def test_分からないをはいに補わない(self):
        need = ["入口に届いた", "必要本文を受け取った", "private保存成功"]
        self.assertEqual(kanzen.kimeru({"入口に届いた": "はい", "必要本文を受け取った": "はい"}, need)[0], None)
        self.assertEqual(kanzen.kimeru({k: "はい" for k in need[:2]} | {"private保存成功": "分からない"}, need)[0], None)
        self.assertEqual(kanzen.kimeru({k: "はい" for k in need[:2]} | {"private保存成功": "いいえ"}, need)[0], False)
        self.assertEqual(kanzen.kimeru({k: "はい" for k in need}, need)[0], True)
        self.assertEqual(kanzen.kimeru({}, [])[0], None)

    def test_200で0行は完全観測にしない(self):
        self.assertEqual(kanzen.zero_gyou(12, 0, None), "分からない")
        self.assertEqual(kanzen.zero_gyou(12, 0, True), "はい")
        self.assertEqual(kanzen.zero_gyou(0, 0, None), "はい")

    def test_急減(self):
        self.assertTrue(kanzen.kyugen(range(10), range(5)))
        self.assertFalse(kanzen.kyugen(range(10), range(6)))

    def test_差分は完全観測どうしだけ(self):
        a = {"kanzen": True, "moto": "u", "houshiki": "html"}
        self.assertTrue(kanzen.sabun_dashite_yoi(a, dict(a))[0])
        self.assertFalse(kanzen.sabun_dashite_yoi(None, a)[0])
        self.assertFalse(kanzen.sabun_dashite_yoi(a, dict(a, kanzen=None))[0])
        self.assertFalse(kanzen.sabun_dashite_yoi(dict(a, kanzen=False), a)[0])
        self.assertFalse(kanzen.sabun_dashite_yoi(a, dict(a, moto="v"))[0])
        self.assertFalse(kanzen.sabun_dashite_yoi(a, dict(a, houshiki="pdf"))[0])


class 一覧(Oki):

    def test_一覧は候補を先に出し_指紋を載せる(self):
        self.cards["kouho"] = yoi_card(統括判定案="", 移行元={"旧値": "取ってよい", "確認日": "2026-09-21",
                                                        "根拠": "統括が読んだ", "在りか": "https://x"})
        self.cards["dame"] = yoi_card(統括判定案="", 移行元={"旧値": "取ってはいけない"})
        self.kaku_all()
        md = kado.ichiran_md(self.mon())
        self.assertLess(md.index("`kouho`"), md.index("`dame`"))
        self.assertIn(kado.card_shimon(self.cards["kouho"]), md)
        self.assertIn("| 取ってよい候補 | 1 |", md)

    def test_読んだ日の無い取ってよいは候補にしない(self):
        self.assertFalse(kado.koho_ka({"移行元": {"旧値": "取ってよい", "根拠": "x", "在りか": "y"}}))
        self.assertFalse(kado.koho_ka({"移行元": {"旧値": "未確認", "確認日": "2026-09-21", "根拠": "x", "在りか": "y"}}))


YOYAKU_REPO = "VirgoB77/kujiraya-aite-yoyaku"

# 別のプロセス（＝別の実行）として予約だけを試す。**合図のファイルが出るまで待ってから一斉に**
KYOUSOU = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
from common import kado
root, hi, aite, junbi, go = sys.argv[2:7]
k = kado.Kado(root, "tameshi-repo", "kujiraya archive bot", today=hi)
open(junbi, "w").close()
while not os.path.exists(go):
    time.sleep(0.002)
try:
    k._yoyaku_shoumei(aite)
    print("TOTTA")
except kado.Tomeru as e:
    print("TORENAI " + str(e))
'''


class ToriniKitaraTokeiWoSusumeru(dict):
    """偽の相手の答え。決めた URL が来たら、偽の時計を進める（途中で0時を越えた形を作る）。"""

    def __init__(self, kotae, tokei, url, saki):
        super().__init__(kotae)
        self.tokei, self.url, self.saki = tokei, url, saki

    def get(self, k, default=None):
        if k == self.url:
            self.tokei[0] = self.saki
        return super().get(k, default)


class 日付の関所(Oki):
    """外へ出す直前に、**いまの日本時間の日付が RUN_DATE と同じか**を見る。違えば、そこから先は出さない。"""

    def test_日付をまたいでいたら_robotstxtも出さない(self):
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(now=lambda: jikoku("2026-09-26", "00:00:05")))
        self.assertEqual(self.nise.kita, [])

    def test_走っている途中で0時を越えたら_そこから先は出さない(self):
        self.shounin()
        tokei = [jikoku("2026-09-25", "23:59:50")]
        u2 = "https://%s/data/two.html" % HOST
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN, u2: HONBUN},
                     now=lambda: tokei[0])
        self.assertEqual(self.toru(k), b"<html>ok</html>")
        kita = list(self.nise.kita)
        tokei[0] = jikoku("2026-09-26", "00:00:01")
        with self.assertRaises(kado.Tomeru):
            self.toru(k, url=u2)
        self.assertEqual(self.nise.kita, kita)
        self.assertTrue(any(x[2] == "日付で止めた" for x in k.kiroku))

    def test_待っている間に0時を越えたら_出さない(self):
        """同じ相手へ続けて出すときの待ち（5秒・Crawl-delay）の間に日付が変わった形。"""
        self.shounin()
        tokei = [jikoku("2026-09-25", "23:59:50")]    # robots.txt → 5秒 → 本体 → 5秒 → 0時
        u2 = "https://%s/data/two.html" % HOST
        nise = Nise({"https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN, u2: HONBUN})
        k = kado.Kado(self.root, REPO, UA, kansoku_demoto=True, env=self.env, transport=nise, run_id="run-1",
                      now=lambda: tokei[0],
                      sleep=lambda n: tokei.__setitem__(0, tokei[0] + n))
        self.nise = nise
        self.assertEqual(self.toru(k), b"<html>ok</html>")
        kita = list(nise.kita)
        with self.assertRaises(kado.Tomeru):
            self.toru(k, url=u2)       # 5秒待つ → 0時を越える
        self.assertEqual(nise.kita, kita)
        self.assertGreaterEqual(tokei[0], jikoku("2026-09-26", "00:00:00"))

    def test_転送の先へも_日付を見てから出す(self):
        self.shounin()
        tokei = [jikoku("2026-09-25", "23:59:58")]
        u2 = "https://%s/data/two.html" % HOST
        kotae = ToriniKitaraTokeiWoSusumeru(
            {"https://%s/robots.txt" % HOST: ROBOTS_OK, URL: (302, {"Location": u2}, b""), u2: HONBUN},
            tokei, URL, jikoku("2026-09-26", "00:00:01"))
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(kotae, now=lambda: tokei[0]))
        self.assertNotIn(u2, self.nise.kita)

    def test_robotstxtの転送の先へも_日付を見てから出す(self):
        self.shounin()
        tokei = [jikoku("2026-09-25", "23:59:58")]
        rb = "https://%s/robots.txt" % HOST
        rb2 = "http://%s/robots.txt" % HOST
        kotae = ToriniKitaraTokeiWoSusumeru(
            {rb: (301, {"Location": rb2}, b""), rb2: ROBOTS_OK, URL: HONBUN},
            tokei, rb, jikoku("2026-09-26", "00:00:01"))
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(kotae, now=lambda: tokei[0]))
        self.assertEqual(self.nise.kita, [rb])

    def test_同じ日の転送は辿れる(self):
        """日付の関所は、日付が同じなら転送を止めない（止めすぎていないことの確かめ）。"""
        self.shounin()
        rb = "https://%s/robots.txt" % HOST
        rb2 = "http://%s/robots.txt" % HOST
        self.assertEqual(self.toru(self.mon({rb: (301, {"Location": rb2}, b""), rb2: ROBOTS_OK,
                                             URL: HONBUN})), b"<html>ok</html>")
        self.assertEqual(self.nise.kita, [rb, rb2, URL])

    def test_RUN_DATEと実行の日付が違えば止まる(self):
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(today="2026-09-24", now=lambda: jikoku("2026-09-24", "10:00:00")))
        self.assertEqual(self.nise.kita, [])


class 予約台帳(Oki):
    """repo横断・同じ相手・同じ JST 日の予約。**台帳は一時フォルダの bare repo。外へは出ない。**"""

    def setUp(self):
        super().setUp()
        self.gh = tempfile.mkdtemp()
        self.bare = os.path.join(self.gh, "VirgoB77", "kujiraya-aite-yoyaku.git").replace("\\", "/")
        os.makedirs(self.bare)
        git(self.bare, "init", "-q", "--bare", "-b", "main")
        tane = os.path.join(self.gh, "tane")
        subprocess.run(["git", "clone", "-q", self.bare, tane], check=True, capture_output=True)
        with open(os.path.join(tane, "README.md"), "w", encoding="utf-8") as f:
            f.write("予約台帳\n")
        git(tane, "add", "-A")
        git(tane, "commit", "-q", "-m", "はじめ")
        git(tane, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.tane = tane
        self.daicho = {"yoyaku": {"repo": YOYAKU_REPO, "hitsuyou": True},
                       "aite": {"ためし県": {"host": [HOST], "担当": REPO, "id": "tameshi-ken"},
                                "ためし市": {"host": ["www.example-shi.lg.jp"], "担当": REPO,
                                           "id": "tameshi-shi"}}}
        self.kaku_all()
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "予約台帳つきの相手台帳")
        self.env = self.run_env("a")

    def tearDown(self):
        for r, _, fs in os.walk(self.gh):              # git の中身は読み取り専用（Windows では消せない）
            for n in fs:
                os.chmod(os.path.join(r, n), 0o666)
        shutil.rmtree(self.gh, ignore_errors=True)
        super().tearDown()

    def clone(self, na):
        d = os.path.join(self.gh, "run-" + na)
        if not os.path.isdir(d):
            subprocess.run(["git", "clone", "-q", self.bare, d], check=True, capture_output=True)
        return d

    def run_env(self, na, job="shutoku", attempt="1", hajime="2026-09-25T07:00:05+09:00", **kae):
        e = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1", "RUN_DATE": "2026-09-25",
             "RUN_HAJIME": hajime, "YOYAKU_DIR": self.clone(na), "YOYAKU_REPO": YOYAKU_REPO,
             "GITHUB_REPOSITORY": "VirgoB77/" + REPO, "GITHUB_RUN_ID": "run-" + na,
             "GITHUB_RUN_ATTEMPT": attempt, "GITHUB_JOB": job,
             "GITHUB_WORKFLOW_REF": "VirgoB77/%s/.github/workflows/tameshi.yml@refs/heads/main" % REPO}
        e.update(kae)
        return e

    def yoyaku_ni_aru(self, hi="2026-09-25", aid="tameshi-ken", michi="yoyaku"):
        subprocess.run(["git", "-C", self.tane, "pull", "-q", "origin", "main"], capture_output=True)
        p = os.path.join(self.tane, michi, hi, aid + ".json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

    def honsu(self):
        r = subprocess.run(["git", "-C", self.bare, "rev-list", "--count", "main"],
                           capture_output=True, text=True)
        return int(r.stdout.strip())

    def test_予約が取れたら通信へ進み_予約が台帳に残る(self):
        self.shounin()
        self.assertEqual(self.toru(self.mon()), b"<html>ok</html>")
        s = self.yoyaku_ni_aru()
        self.assertEqual((s["schema"], s["hi"], s["aite"], s["run_id"], s["run_attempt"], s["job"]),
                         (1, "2026-09-25", "tameshi-ken", "run-a", "1", "shutoku"))
        self.assertEqual(s["repo"], "VirgoB77/" + REPO)

    def test_ほかの実行が今日の予約を持っていれば止まる_通信は出ない(self):
        self.shounin()
        self.env = self.run_env("b")
        self.toru(self.mon(run_id="run-b"))
        self.env = self.run_env("a")
        k = self.mon(run_id="run-a")
        os.remove(self.p("data/ref/aite-kyou.json"))       # 置き場の中の控えが無くても、台帳で止まる
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_同じ実行の後続は予約を使い回す(self):
        self.shounin()
        self.toru(self.mon(run_id="run-a"))
        n = self.honsu()
        self.env = self.run_env("a2", GITHUB_RUN_ID="run-a")     # 同じ job の別の処理（作業木は別）
        self.assertEqual(self.toru(self.mon(run_id="run-a")), b"<html>ok</html>")
        self.assertEqual(self.honsu(), n)                      # 予約は増えていない

    def test_attemptかjobが違えば別の実行として止まる(self):
        for kae in ({"attempt": "2"}, {"job": "betsu-no-job"}):
            with self.subTest(kae=kae):
                self.setUp()
                self.shounin()
                self.toru(self.mon(run_id="run-a"))
                self.env = self.run_env("a3", GITHUB_RUN_ID="run-a", **kae)
                if os.path.exists(self.p("data/ref/aite-kyou.json")):
                    os.remove(self.p("data/ref/aite-kyou.json"))
                with self.assertRaises(kado.Tomeru):
                    self.toru(self.mon(run_id="run-a-betsu"))
                self.assertEqual(self.nise.kita, [])

    def test_前提が欠ければ通信の前に止まり_台帳にも触らない(self):
        self.shounin()
        n = self.honsu()
        kesu = {"RUN_HAJIME": None, "GITHUB_JOB": None, "GITHUB_RUN_ATTEMPT": None,
                "YOYAKU_DIR": None, "YOYAKU_REPO": None}
        for k, v in list(kesu.items()) + [("RUN_HAJIME", "2026-09-25T23:10:00+09:00"),
                                          ("RUN_HAJIME", "2026-09-24T07:00:00+09:00"),
                                          ("RUN_HAJIME", "2026-09-25T07:00:00Z"),
                                          ("YOYAKU_REPO", "VirgoB77/betsu")]:
            with self.subTest(k=k, v=v):
                self.env = self.run_env("a")
                if v is None:
                    del self.env[k]
                else:
                    self.env[k] = v
                kd = self.mon()
                self.assertTrue(any("予約台帳" in r for r in kd.card_mon(
                    "tameshi", hozon_saki=os.path.join(self.kinko, "raw"))))
                with self.assertRaises(kado.Tomeru):
                    self.toru(kd)
                self.assertEqual(self.nise.kita, [])
        self.assertEqual(self.honsu(), n)

    def test_相手IDが無ければ止まる(self):
        del self.daicho["aite"]["ためし県"]["id"]
        self.kaku_all()
        git(self.root, "commit", "-qam", "id を消す")
        self.shounin()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        self.assertEqual(self.nise.kita, [])

    def test_台帳が違う_取り込めない_pushできないと止まる(self):
        self.shounin()
        # 取り込み先が別の repo
        betsu = os.path.join(self.gh, "betsu.git").replace("\\", "/")
        subprocess.run(["git", "clone", "-q", "--bare", self.bare, betsu], check=True, capture_output=True)
        d = os.path.join(self.gh, "run-x")
        subprocess.run(["git", "clone", "-q", betsu, d], check=True, capture_output=True)
        self.env = self.run_env("a", YOYAKU_DIR=d)
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(run_id="x"))
        self.assertEqual(self.nise.kita, [])
        # push の先が無い（取り込みはできる）
        self.env = self.run_env("p")
        git(self.env["YOYAKU_DIR"], "remote", "set-url", "--push", "origin",
            os.path.join(self.gh, "nai", "VirgoB77", "kujiraya-aite-yoyaku.git").replace("\\", "/"))
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(run_id="p"))
        self.assertEqual(self.nise.kita, [])
        self.assertIsNone(self.yoyaku_ni_aru())
        # push は通るのに、台帳には入らない（別の置き場へ書いた）。書いたあとの読み直しで止まる
        otori = os.path.join(self.gh, "otori", "VirgoB77", "kujiraya-aite-yoyaku.git").replace("\\", "/")
        os.makedirs(otori)
        git(otori, "init", "-q", "--bare", "-b", "main")
        self.env = self.run_env("o")
        git(self.env["YOYAKU_DIR"], "remote", "set-url", "--push", "origin", otori)
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(run_id="o"))
        self.assertEqual(self.nise.kita, [])
        self.assertIsNone(self.yoyaku_ni_aru())
        # 台帳そのものが無い（取り込めない）
        self.env = self.run_env("f")
        os.rename(self.bare, self.bare + ".kieta")      # Windows では git の中身をすぐ消せないので、場所を変える
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(run_id="f"))
        self.assertEqual(self.nise.kita, [])

    def test_読めない予約のファイルがあれば止まる(self):
        self.shounin()
        d = os.path.join(self.tane, "yoyaku", "2026-09-25")
        os.makedirs(d)
        with open(os.path.join(d, "tameshi-ken.json"), "w", encoding="utf-8") as f:
            f.write("{こわれている")
        git(self.tane, "add", "-A")
        git(self.tane, "commit", "-q", "-m", "こわれた予約")
        git(self.tane, "push", "-q", "origin", "HEAD:refs/heads/main")
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon())
        self.assertEqual(self.nise.kita, [])

    def test_予約は解放しない_失敗した実行の予約も残る(self):
        self.shounin()
        k = self.mon({"https://%s/robots.txt" % HOST: (403, {}, b""), URL: HONBUN}, run_id="run-a")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)                          # robots で止まった（予約は取ってある）
        self.assertIsNotNone(self.yoyaku_ni_aru())
        os.remove(self.p("data/ref/aite-kyou.json"))       # 置き場の中の控えが無くても、台帳で止まる
        self.env = self.run_env("b")
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(run_id="run-b"))   # 同じ日の別の実行は、もう行けない
        self.assertEqual(self.nise.kita, [])

    def test_日付をまたいだ回は_予約台帳にも書かない(self):
        """0時を越えてから初めてその相手へ行こうとした形。**予約を書く前に止まる**（翌日の枠を取らない）。"""
        self.shounin()
        self.env = self.run_env("a", hajime="2026-09-25T22:00:00+09:00")
        mae = self.honsu()
        with self.assertRaises(kado.Tomeru):
            self.toru(self.mon(now=lambda: jikoku("2026-09-26", "00:00:05")))
        self.assertEqual(self.nise.kita, [])
        self.assertEqual(self.honsu(), mae)
        self.assertIsNone(self.yoyaku_ni_aru())
        self.assertIsNone(self.yoyaku_ni_aru(hi="2026-09-26"))

    def test_予約済みでも_日付をまたいだら出さない(self):
        """22時台に始まった回が、0時を越えたあと、予約を使い回して翌日の分を取りに行かない。"""
        self.shounin()
        self.env = self.run_env("a", hajime="2026-09-25T22:00:00+09:00")
        tokei = [jikoku("2026-09-25", "23:59:50")]
        self.assertEqual(self.toru(self.mon(now=lambda: tokei[0])), b"<html>ok</html>")
        kita = list(self.nise.kita)
        tokei[0] = jikoku("2026-09-26", "00:00:05")
        k2 = self.mon(now=lambda: tokei[0])          # 同じ実行の後続（予約を使い回す道）
        self.nise.kita = kita
        with self.assertRaises(kado.Tomeru):
            self.toru(k2)
        self.assertEqual(self.nise.kita, kita)
        self.assertIsNotNone(self.yoyaku_ni_aru())
        self.assertIsNone(self.yoyaku_ni_aru(hi="2026-09-26"))

    def betsu_no_yoyaku(self):
        """台帳に、前の日の予約を1つ置く（消す・変える・改名する相手）。"""
        d = os.path.join(self.tane, "yoyaku", "2026-09-24")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "tameshi-shi.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"schema": 1, "hi": "2026-09-24", "aite": "tameshi-shi", "repo": "VirgoB77/betsu",
                       "run_id": "9", "run_attempt": "1", "job": "j", "workflow": ""}, f)
        git(self.tane, "add", "-A")
        git(self.tane, "commit", "-q", "-m", "前の日の予約")
        git(self.tane, "push", "-q", "origin", "HEAD:refs/heads/main")
        return "yoyaku/2026-09-24/tameshi-shi.json"

    def ki(self):
        r = subprocess.run(["git", "-C", self.bare, "ls-tree", "-r", "main"], capture_output=True, text=True)
        return r.stdout

    def test_予約のcommitに_予定の1件の追加でないものが混ざれば_pushしない(self):
        self.shounin()
        mae_no = self.betsu_no_yoyaku()
        mae_ki, mae_honsu = self.ki(), self.honsu()

        def sakujo(d):
            git(d, "rm", "-q", mae_no)

        def henkou(d):
            with open(os.path.join(d, mae_no), "a", encoding="utf-8") as f:
                f.write("\n")
            git(d, "add", mae_no)

        def kaimei(d):
            git(d, "mv", mae_no, "yoyaku/2026-09-24/tameshi-shi2.json")

        def yotei_gai(d):
            with open(os.path.join(d, "yotei-gai.txt"), "w", encoding="utf-8") as f:
                f.write("x\n")
            git(d, "add", "yotei-gai.txt")

        def yotei_gai_ato(d):
            # 予約のファイルより後ろに並ぶ名前（差分の並びで、予約が先頭に来る形）
            with open(os.path.join(d, "zzz-yotei-gai.txt"), "w", encoding="utf-8") as f:
                f.write("x\n")
            git(d, "add", "zzz-yotei-gai.txt")

        def readme(d):
            with open(os.path.join(d, "README.md"), "a", encoding="utf-8") as f:
                f.write("書き換え\n")
            git(d, "add", "README.md")

        def nakami_ga_kawaru(d):
            # 入れるときにバイトを変える仕掛け（clean フィルタ）。予定と違うバイトが台帳に入る形
            with open(os.path.join(d, ".git", "info", "attributes"), "w", encoding="utf-8") as f:
                f.write("*.json filter=kaeru\n")
            git(d, "config", "filter.kaeru.clean", "sed s/tameshi.yml/kaeta.yml/")

        def shikkou(d):
            # 予約のファイルが「実行できるファイル」として入る形（通常のファイルの追加ではない）。
            # 先に同じ名前を実行できる形で置いておく。fileMode を切ると、git add は置いてある形を保つ
            michi = os.path.join("yoyaku", "2026-09-25", "tameshi-ken.json")
            os.makedirs(os.path.join(d, "yoyaku", "2026-09-25"), exist_ok=True)
            with open(os.path.join(d, michi), "w", encoding="utf-8") as f:
                f.write("{}\n")
            git(d, "config", "core.fileMode", "false")
            git(d, "add", michi)
            git(d, "update-index", "--chmod=+x", michi)

        for na, f in (("sakujo", sakujo), ("henkou", henkou), ("kaimei", kaimei),
                      ("yoteigai", yotei_gai), ("yoteigaiato", yotei_gai_ato), ("readme", readme),
                      ("nakami", nakami_ga_kawaru), ("shikkou", shikkou)):
            with self.subTest(na=na):
                self.env = self.run_env(na)
                f(self.env["YOYAKU_DIR"])
                with self.assertRaises(kado.Tomeru):
                    self.toru(self.mon(run_id="run-" + na))
                self.assertEqual(self.nise.kita, [])
                self.assertEqual(self.ki(), mae_ki)          # 台帳は1バイトも変わらない
                self.assertEqual(self.honsu(), mae_honsu)
        self.assertIsNone(self.yoyaku_ni_aru())

    def test_予約のcommitの親が台帳の先頭でなければ_pushしない(self):
        """間に余分な commit（中身は空）が挟まった形。差分は1件の追加でも、送る commit が2つになる。"""
        self.shounin()
        mae_ki, mae_honsu = self.ki(), self.honsu()
        moto_git = kado._git

        def git_kawari(root, *args):
            r = moto_git(root, *args)
            if args[:3] == ("checkout", "--quiet", "-B") and r.returncode == 0:
                moto_git(root, "-c", "user.name=x", "-c", "user.email=x@x.invalid",
                         "commit", "--quiet", "--allow-empty", "-m", "よけい")
            return r

        kado._git = git_kawari
        try:
            with self.assertRaises(kado.Tomeru) as c:
                self.toru(self.mon())
        finally:
            kado._git = moto_git
        self.assertIn("親", str(c.exception))
        self.assertEqual(self.nise.kita, [])
        self.assertEqual((self.ki(), self.honsu()), (mae_ki, mae_honsu))

    def test_書いた予約があとから変えられたら止まる(self):
        """push のあと、取り込み直すまでの間に、だれかが自分の予約を書き換えた形。"""
        self.shounin()
        moto_git = kado._git
        jotai = {"pushed": False, "kaeta": False}
        kaeru = os.path.join(self.gh, "kaeru")

        def git_kawari(root, *args):
            if args and args[0] == "push":
                r = moto_git(root, *args)
                jotai["pushed"] = r.returncode == 0
                return r
            if args and args[0] == "fetch" and jotai["pushed"] and not jotai["kaeta"]:
                jotai["kaeta"] = True
                subprocess.run(["git", "clone", "-q", self.bare, kaeru], check=True, capture_output=True)
                p = os.path.join(kaeru, "yoyaku", "2026-09-25", "tameshi-ken.json")
                s = json.load(open(p, encoding="utf-8"))
                s["workflow"] = "かきかえ"                   # 実行の識別はそのまま（読み直しでは気づけない形）
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(s, f, ensure_ascii=False)
                git(kaeru, "commit", "-qam", "かきかえ")
                git(kaeru, "push", "-q", "origin", "HEAD:refs/heads/main")
            return moto_git(root, *args)

        kado._git = git_kawari
        try:
            with self.assertRaises(kado.Tomeru) as c:
                self.toru(self.mon())
        finally:
            kado._git = moto_git
        self.assertTrue(jotai["kaeta"])
        self.assertIn("あとから変えられた", str(c.exception))
        self.assertEqual(self.nise.kita, [])

    def test_予約台帳の決まりが無い_形が違えば止まる(self):
        self.shounin()
        for yoyaku in (None, "要る", {}, {"repo": YOYAKU_REPO}, {"hitsuyou": "true", "repo": YOYAKU_REPO},
                       {"hitsuyou": True}, {"hitsuyou": 1, "repo": YOYAKU_REPO}):
            with self.subTest(yoyaku=yoyaku):
                if yoyaku is None:
                    self.daicho.pop("yoyaku", None)
                else:
                    self.daicho["yoyaku"] = yoyaku
                self.kaku_all()
                git(self.root, "commit", "-qam", "形を変えた")
                with self.assertRaises(kado.Tomeru):
                    self.toru(self.mon())
                self.assertEqual(self.nise.kita, [])
        self.assertIsNone(self.yoyaku_ni_aru())

    def test_一度予約できなかった相手は_この回もう試さない(self):
        """取り込みに1回失敗したら、あとで台帳が戻っても、この回はその相手へ行かない。"""
        self.shounin()
        os.rename(self.bare, self.bare + ".kieta")
        K = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.toru(K)
        os.rename(self.bare + ".kieta", self.bare)
        with self.assertRaises(kado.Tomeru):
            self.toru(K)
        self.assertEqual(self.nise.kita, [])
        self.assertIsNone(self.yoyaku_ni_aru())

    def test_予約が要らないと台帳が言っていれば使わない(self):
        self.daicho["yoyaku"]["hitsuyou"] = False
        self.kaku_all()
        git(self.root, "commit", "-qam", "要らない")
        self.shounin()
        del self.env["YOYAKU_DIR"]
        self.assertEqual(self.toru(self.mon()), b"<html>ok</html>")
        self.assertIsNone(self.yoyaku_ni_aru())

    def test_積み直しは上限の回数で止まり_外へ出ない(self):
        """push が毎回「先頭の食い違い」で断られ続けたら、**上限の回数で止まる**（2026-09-26・⑦C）。

        台帳の bare repo に、どの push も断る pre-receive を置き、来た回数を数える。

          ・上限の値は、いまのコードでは 5（YOYAKU_KAISU）。値そのものも決まりとして見る
          ・push が来た回数が、ちょうど上限と同じ（上限の次の回へ進まない）
          ・最後は Tomeru（止まる）。理由は「予約の競合が続いた」。相手への通信は1本も出ない。台帳に予約は入らない

        pre-receive は、上限より3回あとからは通すように変わる。**上限を外すと、そこで通って「取れた」に
        なり、この検査が鳴る**（止まらずに回り続けて、検査そのものが終わらない形にしないため）。
        """
        self.assertEqual(kado.YOYAKU_KAISU, 5)
        self.shounin()
        kazu = os.path.join(self.gh, "kita.txt").replace("\\", "/")
        hook = os.path.join(self.bare, "hooks", "pre-receive")
        with open(hook, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\n"
                    "echo x >> '%s'\n"
                    "n=$(wc -l < '%s')\n"
                    "if [ \"$n\" -le %d ]; then echo 'cannot lock ref: 試験で断る' >&2; exit 1; fi\n"
                    "exit 0\n" % (kazu, kazu, kado.YOYAKU_KAISU + 3))
        os.chmod(hook, 0o755)
        with self.assertRaises(kado.Tomeru) as c:
            self.toru(self.mon(run_id="kaisu"))
        with open(kazu, encoding="utf-8") as f:
            kita = sum(1 for _ in f)
        self.assertEqual(kita, kado.YOYAKU_KAISU)
        self.assertIn("予約の競合が %d 回続いた" % kado.YOYAKU_KAISU, str(c.exception))
        self.assertEqual(self.nise.kita, [])
        self.assertIsNone(self.yoyaku_ni_aru())

    def kyousou(self, aite_a, aite_b, hi):
        """2つの実行（別のプロセス）に、一斉に予約させる。返り値は2つの出力。"""
        go = os.path.join(self.gh, "go-" + hi)
        procs = []
        for na, aite in (("a", aite_a), ("b", aite_b)):
            e = dict(os.environ)
            e.update(self.run_env(na, job="job-" + na, hajime=hi + "T07:00:00+09:00",
                                  RUN_DATE=hi, GITHUB_RUN_ID="run-" + na))
            junbi = os.path.join(self.gh, "junbi-%s-%s" % (hi, na))
            procs.append((junbi, subprocess.Popen(
                [sys.executable, "-c", KYOUSOU, HERE, self.root, hi, aite, junbi, go],
                env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")))
        while not all(os.path.exists(j) for j, _ in procs):
            if any(p.poll() is not None for _, p in procs):
                break
            time.sleep(0.005)
        open(go, "w").close()
        return [p.communicate(timeout=120)[0].strip() for _, p in procs]

    def test_同じ相手を同時に予約したら取得権は1つだけ(self):
        for i in range(1, 11):
            hi = "2026-10-%02d" % i
            with self.subTest(hi=hi):
                out = self.kyousou("ためし県", "ためし県", hi)
                self.assertEqual(sum(o.startswith("TOTTA") for o in out), 1, out)
                # 負けた側は「push できない」ではなく、台帳を読み直して「ほかの実行が持つ」で止まる
                self.assertTrue(any("ほかの実行" in o for o in out if o.startswith("TORENAI")), out)
                s = self.yoyaku_ni_aru(hi=hi)
                self.assertIn(s["job"], ("job-a", "job-b"))

    def test_別の相手なら同時でも両方取れる_積み直す(self):
        for i in range(1, 6):
            hi = "2026-11-%02d" % i
            with self.subTest(hi=hi):
                out = self.kyousou("ためし県", "ためし市", hi)
                self.assertEqual(sum(o.startswith("TOTTA") for o in out), 2, out)

    def test_強制pushも削除もしない(self):
        """予約台帳には書き足すだけ。**門のコードに、強制 push・削除の書き方が無い。**"""
        src = open(os.path.join(HERE, "common", "kado.py"), encoding="utf-8").read()
        for w in ("--force", "--delete", "+HEAD", "+refs", "+%s", "--mirror", "-f\"", "push\", \"-f"):
            self.assertNotIn(w, src, w)
        pushes = [l for l in src.splitlines() if '"push"' in l and "_git(" in l]
        self.assertEqual(len(pushes), 1)
        # 送るのは、照合した自分の commit（mine）を main へ、の1通りだけ（頭に + を付けない）
        self.assertIn('"%s:refs/heads/main" % mine)', pushes[0])


class 知らない使い方で止まる(unittest.TestCase):

    def test_知らない引数なら終了コードが0でない(self):
        for hikisu in (["--zzz-shiranai-hikisu"], [], ["ichiran"], ["ichiran", "a", "b", "c"]):
            with self.subTest(hikisu=hikisu):
                r = subprocess.run([sys.executable, os.path.join(HERE, "common", "kado.py")] + hikisu,
                                   cwd=HERE, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


class import_しただけで閉じる(unittest.TestCase):

    def test_門を始めていない通信は出ない(self):
        urllib.request.install_opener(urllib.request.build_opener(kado._Tozasu()))
        with self.assertRaises(kado.Tomeru):
            urllib.request.urlopen("https://example.invalid/", timeout=1)


def konkyo5_card(**kae):
    """根拠5（公開された事実・数値の、通常の公開経路からの観測）の、そろったカード。"""
    c = yoi_card(**{
        "source種別": "民間一次", "肯定根拠番号": "5",
        "判定理由": "店名・価格・日時などの事実を、ログイン不要の一覧から GET で観測する。"
                    "STOP 条件のどれにも当たらないことを、規約と robots で確かめた",
        "重要な原文": "「当サイトの文章・画像の無断転載を禁じます」（利用規約 第5条）",
        "STOP条件の確認": {
            "robots": "当たらない：/robots.txt は対象の道すじを拒否していない（2026-09-29 に確認）",
            "アクセス制御": "当たらない：ログイン・同意画面・CAPTCHA なしで一覧が開ける",
            "明示的なbot禁止": "当たらない：規約・ヘルプを読み、bot・crawler・自動取得の名指しの禁止が無かった",
            "相手からの回答": "当たらない：問い合わせたことが無い（止める回答を受けていない）",
            "拒否状態": "当たらない：確認のとき 401・403・429・503 は返っていない",
            "負荷": "当たらない：同時1本・5秒以上・同じ相手に1日1回",
            "同意済みの契約": "当たらない：会員・API の同意をしていない（web の route だけ）",
            "個人情報": "当たらない：個人情報・個票の欄はすべて決めてある（カードの欄）",
            "二次・集約": "当たらない：相手自身の一次の掲載",
            "想定外": "当たらない：規約・robots・運営主体・転送先・取得方法に変わりが無い",
        },
    })
    c.update(kae)
    return c


class 公開情報の取得と事実データの利用(Oki):
    """正本 3.4a（2026-09-29）。**明示の許可が書いていないことは、それだけでは止める理由にしない。**
    そのかわり、STOP 条件を全部確かめた記録と、運営者承認と、門のほかの条件が要る。"""

    def test_根拠5は_条件がそろって運営者が承認すれば門を通る(self):
        self.cards["tameshi"] = konkyo5_card()
        self.kaku_all()
        self.shounin()
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "取ってよい", kado._kaketeiru(self.cards["tameshi"]))
        self.assertEqual(self.toru(k), b"<html>ok</html>")

    def test_運営者承認が無ければ_根拠5でも通らない(self):
        self.cards["tameshi"] = konkyo5_card()
        self.kaku_all()
        k = self.mon()
        self.assertEqual(k.seishiki("tameshi"), "規約未確定")
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_該当文言なしだけでは_根拠5でも取ってよいにならない(self):
        self.assertEqual(kado.seishiki_jotai(konkyo5_card(判定理由="該当文言なし"), True), "規約未確定")
        j = dict(konkyo5_card()["STOP条件の確認"], 明示的なbot禁止="該当文言なし")
        self.assertEqual(kado.seishiki_jotai(konkyo5_card(**{"STOP条件の確認": j}), True), "規約未確定")

    def test_STOP条件の確認が1つでも欠ければ根拠5にならない(self):
        for k in kado.STOP_JOKEN:
            with self.subTest(k=k):
                j = dict(konkyo5_card()["STOP条件の確認"])
                del j[k]
                c = konkyo5_card(**{"STOP条件の確認": j})
                self.assertIn(k, "、".join(kado._kaketeiru(c)))
                self.assertEqual(kado.seishiki_jotai(c, True), "規約未確定")

    def test_明示的なbot禁止はSTOP_どの根拠でも(self):
        j = dict(konkyo5_card()["STOP条件の確認"],
                 明示的なbot禁止="当たる：利用規約 第7条「ロボット・クローラーによる自動取得を禁止します」")
        for n in ("5", "1"):
            with self.subTest(n=n):
                self.assertEqual(kado.seishiki_jotai(
                    konkyo5_card(肯定根拠番号=n, **{"STOP条件の確認": j}), True), "規約未確定")

    def test_robotsの拒否はSTOP_カードに書いても_相手のrobotsでも(self):
        j = dict(konkyo5_card()["STOP条件の確認"], robots="当たる：/robots.txt が対象の道すじを Disallow")
        self.assertEqual(kado.seishiki_jotai(konkyo5_card(**{"STOP条件の確認": j}), True), "規約未確定")
        # カードでは「当たらない」でも、相手の robots が拒めば、門は通信を出さない
        self.cards["tameshi"] = konkyo5_card()
        self.kaku_all()
        self.shounin()
        k = self.mon({"https://%s/robots.txt" % HOST: (200, {"Content-Type": "text/plain"},
                                                        b"User-agent: *\nDisallow: /data/\n"),
                      URL: HONBUN})
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertNotIn(URL, self.nise.kita)

    def test_STOP条件の確認が未確認や判断要なら根拠5にならない(self):
        """A-3：カードには「未確認」を空欄と分けて書く。**確かめた中身としては数えない。**"""
        for v in ("未確認", "未確認：まだ確かめていない", "判断要：アクセス制御に当たるか（残作業59）",
                  "分からない", "未決", " 未確認"):
            with self.subTest(v=v):
                c = konkyo5_card()
                c["STOP条件の確認"] = dict(c["STOP条件の確認"], アクセス制御=v)
                self.assertEqual(kado.seishiki_jotai(c, True), "規約未確定")

    def test_商品用継続観測の可否が未確認なら本番に進まない(self):
        self.assertIn("未確認", kado.SHOUHIN_KAHI)
        for c in (konkyo5_card(商品用継続観測の可否="未確認"), yoi_card(商品用継続観測の可否="未確認")):
            with self.subTest(c=c["肯定根拠番号"]):
                self.assertEqual(kado.seishiki_jotai(c, True), "規約未確定")

    def test_置き場のカードの3_4aの欄は決まった形(self):
        """置き場の data/ref/torimoto-card.json の 3.4a の欄を、決まった値だけで書いているか（A-3）。
        **未確認は空欄ではなく「未確認」で書く**（空欄と0を混ぜない）。値の中身が正しいかは見ない。"""
        pth = os.path.join(HERE, "data", "ref", "torimoto-card.json")
        cards = json.load(open(pth, encoding="utf-8"))["cards"]
        self.assertGreater(len(cards), 0)
        for cid, c in cards.items():
            with self.subTest(cid=cid):
                self.assertIn(c.get("route種別"), kado.ROUTE + ("",))
                self.assertTrue(c.get("source_id") == "" or kado.ROUTE_ID.match(c["source_id"]))
                j = c.get("STOP条件の確認")
                self.assertIsInstance(j, dict)
                self.assertEqual(sorted(j), sorted(kado.STOP_JOKEN))
                for v in j.values():
                    self.assertTrue(isinstance(v, str) and v.strip(), "STOP条件の確認は空欄にしない（未確認と書く）")
                    self.assertTrue(kado.STOP_ATARU.match(v) or kado.MIKAKUNIN.match(v) or v.startswith("当たらない"))
                self.assertIn(c.get("商品用継続観測の可否"), kado.SHOUHIN_KAHI)
                self.assertIn(c.get("このrouteのデータを商品に使えるか"), kado.ROUTE_SHOUHIN)
                self.assertTrue((c.get("不確定事項") or "").strip(), "不確定事項は空欄にしない")
                if c.get("このrouteのデータを商品に使えるか") != "使える":
                    self.assertTrue((c.get("商品化の未確認事項") or "").strip(), "商品に使えるかが未確認なら、商品化の未確認事項に書く")

    def test_根拠5は通常の公開経路webのrouteだけ(self):
        for r in ("api", "member"):
            with self.subTest(r=r):
                self.assertEqual(kado.seishiki_jotai(konkyo5_card(route種別=r), True), "規約未確定")

    def test_商品化の未確認だけでは_取得の門を落とさない(self):
        c = konkyo5_card(商品化の未確認事項="どの欄を商品に出すか、画面の設計が未決")
        self.assertEqual(kado.seishiki_jotai(c, True), "取ってよい")
        self.assertEqual(kado.seishiki_jotai(konkyo5_card(不確定事項="取得を止める不確定事項なし"), True),
                         "取ってよい")

    def test_取得を止める不確定事項が残っていれば通らない(self):
        self.assertEqual(kado.seishiki_jotai(konkyo5_card(不確定事項="規約の改定待ち"), True), "規約未確定")

    def test_長期の商品用sourceか読めなければ本番に進まない(self):
        for v in ("読めない", "外す", "", None):
            with self.subTest(v=v):
                self.assertEqual(kado.seishiki_jotai(
                    konkyo5_card(商品用継続観測の可否=v), True), "規約未確定")

    def test_routeと出どころが無ければ取ってよいにならない(self):
        for kae in ({"route種別": ""}, {"route種別": "ftp"}, {"source_id": ""}, {"source_id": "日本語"},
                    {"route種別": "manual"}):
            with self.subTest(kae=kae):
                self.assertEqual(kado.seishiki_jotai(konkyo5_card(**kae), True), "規約未確定")
                # 根拠5 に限らない（根拠1 のカードでも route と出どころが要る）
                self.assertEqual(kado.seishiki_jotai(yoi_card(**kae), True), "規約未確定")

    def test_観測の記録は出どころを持つ(self):
        d = kado.demoto("tameshi", konkyo5_card(), "2026-09-29T07:00:00+09:00", URL)
        self.assertEqual(d, {"source_id": "tameshi-ken", "route_id": "tameshi", "route_kind": "web",
                             "observed_at": "2026-09-29T07:00:00+09:00", "source_url": URL})
        with self.assertRaises(ValueError):
            kado.demoto("tameshi", konkyo5_card(route種別=""), "2026-09-29T07:00:00+09:00")
        with self.assertRaises(ValueError):
            kado.demoto("tameshi", konkyo5_card(), "2026-09-29")        # 時刻・時差が無い
        # manual は記録者の役割と見た URL が要る
        m = konkyo5_card(route種別="manual")
        with self.assertRaises(ValueError):
            kado.demoto("tameshi-m", m, "2026-09-29T07:00:00+09:00")
        d = kado.demoto("tameshi-m", m, "2026-09-29T07:00:00+09:00", URL, recorder_role="運営者")
        self.assertEqual(d["recorder_role"], "運営者")
        self.assertEqual(kado.demoto_tarinai({"demoto": d}), [])
        self.assertIn("recorder_role", kado.demoto_tarinai(
            {"demoto": {k: v for k, v in d.items() if k != "recorder_role"}}))

    def test_API由来の使えない記録を_商品へ混ぜない(self):
        cards = {"tameshi": konkyo5_card(),
                 "tameshi-api": konkyo5_card(route種別="api", 肯定根拠番号="3",
                                             このrouteのデータを商品に使えるか="使えない")}
        t = "2026-09-29T07:00:00+09:00"
        web = {"price": 100, "demoto": kado.demoto("tameshi", cards["tameshi"], t)}
        api = {"price": 100, "demoto": kado.demoto("tameshi-api", cards["tameshi-api"], t)}
        nashi = {"price": 100}
        nokosu, hazushita = kado.shouhin_ni_tsukaeru([web, api, nashi], cards)
        self.assertEqual(nokosu, [web])
        self.assertEqual([r for r, _ in hazushita], [api, nashi])
        self.assertIn("商品に使えない", hazushita[0][1])
        self.assertIn("出どころ", hazushita[1][1])
        # 取得元が「外す」なら、web の route でも商品に使わない
        cards["tameshi"] = konkyo5_card(商品用継続観測の可否="外す")
        self.assertEqual(kado.shouhin_ni_tsukaeru([web], cards)[0], [])


ACTION = "https://%s/resource/?id=21424" % HOST
FORM_ATAI = {"upload_file": "20260901_一覧（令和8年7月）.xlsx", "upload_url": "", "download": "このデータをダウンロード"}
XLSX = (200, {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}, b"PK\x03\x04tameshi")


def form_card(**kae):
    """public_form_post（公開 Web の download form）の、そろったカード。"""
    c = yoi_card(**{
        "route種別": "public_form_post",
        "承認する取得方法": {"URL範囲": [ACTION], "対象種類": "XLSX", "ページ送り・深さ": "なし（1本）",
                         "API/feed": "なし", "承認頻度": "1回"},
        "公開form": {"action": ACTION, "method": "POST", "送る値": dict(FORM_ATAI), "見たページ": ACTION,
                   "見た日": "2026-09-30"}})
    c.update(kae)
    return c


class HTTPメソッドとpublic_form_post(Oki):
    """正本 3.4a。web・api・member は GET だけ。public_form_post は、カードの「公開form」と action・中身が
    1字も違わない POST だけ（Cookie・Authorization・Referer なし）。**POST の転送は辿らない。** GET の転送はこれまでどおり。"""

    def setUp(self):
        super().setUp()
        self.cards["tameshi-f"] = form_card()
        self.kaku_all()

    def fmon(self, kotae=None, **kw):
        return self.mon(kotae if kotae is not None else {"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: XLSX}, **kw)

    def dasu(self, k, cid, req):
        k.install()
        with k.sesshon(cid, hozon_saki=os.path.join(self.kinko, "raw")):
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()

    def post(self, data=None, url=ACTION, **midashi):
        if data is None:
            data = kado.form_nakami(form_card()["公開form"])
        return urllib.request.Request(url, data=data, headers=midashi, method="POST")

    def test_webのGETは変わらない(self):
        self.shounin()
        k = self.mon()
        self.assertEqual(self.toru(k), b"<html>ok</html>")
        self.assertEqual([n[0] for n in self.nise.naka], ["GET", "GET"])          # robots.txt と本文

    def test_webのrouteではGET以外を出さない(self):
        self.shounin()
        k = self.mon()
        for m in ("POST", "HEAD", "PUT", "DELETE", "PATCH"):
            with self.subTest(m=m):
                req = urllib.request.Request(URL, data=b"a=1" if m in ("POST", "PUT", "PATCH") else None, method=m)
                with self.assertRaises(kado.Tomeru) as cm:
                    self.dasu(k, "tameshi", req)
                self.assertIn("route「web」では HTTP %s を出さない" % m, str(cm.exception))     # メソッドの段で止まる
        self.assertEqual(self.nise.kita, [])                                        # robots.txt も取りに行かない

    def test_apiとmemberもGETだけ(self):
        for route in ("api", "member"):
            with self.subTest(route=route):
                cid = "tameshi-" + route
                self.cards[cid] = yoi_card(route種別=route, 肯定根拠番号="3")
                self.kaku_all()
                self.shounin(cid)
                with self.assertRaises(kado.Tomeru) as cm:
                    self.dasu(self.mon(), cid, urllib.request.Request(URL, data=b"a=1", method="POST"))
                self.assertIn("route「%s」では HTTP POST を出さない" % route, str(cm.exception))
                self.assertEqual(self.nise.kita, [])

    def test_公開formと1字も違わないPOSTだけ通す(self):
        self.shounin("tameshi-f")
        k = self.fmon()
        self.assertEqual(self.dasu(k, "tameshi-f", self.post()), (200, b"PK\x03\x04tameshi"))
        m, data, midashi = self.nise.naka[-1]
        self.assertEqual((m, data), ("POST", kado.form_nakami(form_card()["公開form"])))
        import urllib.parse as up
        self.assertEqual(data, up.urlencode(list(FORM_ATAI.items()), encoding="utf-8").encode("ascii"))    # カードに書いた順
        self.assertFalse({"Cookie", "Authorization", "Referer"} & set(midashi))
        self.assertIsNone(k.tensou_saki)

    def test_中身が1字違う_欄を足す_欄が欠けるPOSTは出さない(self):
        self.shounin("tameshi-f")
        k = self.fmon()
        import urllib.parse as up
        for atai in (dict(FORM_ATAI, upload_file=FORM_ATAI["upload_file"][:-1] + "X"),       # 1字違い
                     dict(FORM_ATAI, extra="1"),                                             # 欄を足す
                     {a: b for a, b in FORM_ATAI.items() if a != "upload_url"},              # 欄が欠ける
                     dict(reversed(list(FORM_ATAI.items())))):                               # 並びが違う
            with self.subTest(atai=list(atai)):
                data = up.urlencode(list(atai.items()), encoding="utf-8").encode("ascii")
                with self.assertRaises(kado.Tomeru):
                    self.dasu(k, "tameshi-f", self.post(data=data))
        with self.assertRaises(kado.Tomeru):
            self.dasu(k, "tameshi-f", self.post(data="x=1"))                               # bytes でない中身
        self.assertEqual(self.nise.kita, [])

    def test_actionが違うPOSTは出さない(self):
        self.shounin("tameshi-f")
        k = self.fmon()
        for url in (ACTION + "0", ACTION.replace("21424", "21425"), ACTION.replace("https://", "http://")):
            with self.subTest(url=url):
                with self.assertRaises(kado.Tomeru):
                    self.dasu(k, "tameshi-f", self.post(url=url))
        self.assertEqual(self.nise.kita, [])

    def test_CookieやAuthorizationやRefererの付いたPOSTは出さない(self):
        self.shounin("tameshi-f")
        k = self.fmon()
        for midashi in ({"Cookie": "a=b"}, {"Authorization": "Bearer x"}, {"Proxy-Authorization": "Basic x"},
                        {"Referer": ACTION}):
            with self.subTest(midashi=list(midashi)):
                with self.assertRaises(kado.Tomeru):
                    self.dasu(k, "tameshi-f", self.post(**midashi))
        self.assertEqual(self.nise.kita, [])

    def test_POSTの転送は辿らない_Locationを記録して止める(self):
        self.shounin("tameshi-f")
        saki = "https://%s/files/x.xlsx" % HOST
        k = self.fmon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: (302, {"Location": saki}, b""), saki: XLSX})
        with self.assertRaises(kado.Tomeru):
            self.dasu(k, "tameshi-f", self.post())
        self.assertEqual(k.tensou_saki, saki)
        self.assertNotIn(saki, self.nise.kita)
        self.assertEqual([n[0] for n in self.nise.naka], ["GET", "POST"])            # robots.txt と POST だけ

    def test_GETの転送はこれまでどおり門を通して辿る(self):
        self.shounin()
        saki = "https://%s/data/list2.html" % HOST
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, URL: (302, {"Location": saki}, b""), saki: HONBUN})
        self.assertEqual(self.toru(k), b"<html>ok</html>")
        self.assertIn(saki, self.nise.kita)
        self.assertIsNone(k.tensou_saki)

    def test_POSTに401_403_429_503が返ったらその相手を止める(self):
        self.shounin("tameshi-f")
        for code in (401, 403, 429, 503):
            with self.subTest(code=code):
                kyou = self.p("data", "ref", "aite-kyou.json")
                if os.path.exists(kyou):
                    os.remove(kyou)
                k = self.fmon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: (code, {}, b"")}, run_id="run-%d" % code)
                with self.assertRaises(urllib.error.HTTPError):
                    self.dasu(k, "tameshi-f", self.post())
                self.assertIn("ためし県", k._tomatta)

    def test_公開formの形が違うカードは取ってよいにならない(self):
        f = form_card()["公開form"]
        for kae in ({"公開form": None}, {"公開form": dict(f, method="GET")},
                    {"公開form": dict(f, action=ACTION.replace("https://", "http://"))},
                    {"公開form": dict(f, action="https://hoka.example.jp/resource/?id=1")},
                    {"公開form": dict(f, action="https://%s/other/?id=1" % HOST)},                # URL範囲の外
                    {"公開form": dict(f, 送る値={})}, {"公開form": dict(f, 送る値={"a": 1})},
                    {"公開form": dict(f, 送る値={" ": "x"})},
                    {"公開form": dict(f, 見たページ="https://hoka.example.jp/")},
                    {"公開form": dict(f, 見た日="きのう")},
                    # URL範囲 を合わせても、action は https で対象host の中でなければならない
                    {"公開form": dict(f, action=ACTION.replace("https://", "http://")),
                     "承認する取得方法": dict(form_card()["承認する取得方法"], URL範囲=[ACTION.replace("https://", "http://")])},
                    {"公開form": dict(f, action="https://hoka.example.jp/resource/?id=1"),
                     "承認する取得方法": dict(form_card()["承認する取得方法"], URL範囲=["https://hoka.example.jp/resource/"])}):
            with self.subTest(kae=str(kae)[:60]):
                self.assertEqual(kado.seishiki_jotai(form_card(**kae), True), "規約未確定")
        self.assertEqual(kado.seishiki_jotai(form_card(), True), "取ってよい")
        self.assertIn("public_form_post", kado.ROUTE)

    def test_manualのカードはsesshonに入れない(self):
        """manual は機械の取得をしない。**カードの門（sesshon）で止まる**（メソッドの段より前）。"""
        self.cards["tameshi-m"] = manual_card()
        self.kaku_all()
        self.shounin("tameshi-m")
        k = self.mon()
        with self.assertRaises(kado.Tomeru) as cm:
            with k.sesshon("tameshi-m", hozon_saki=os.path.join(self.kinko, "raw")):
                pass
        self.assertIn("manual", str(cm.exception))
        self.assertEqual(self.nise.kita, [])

    def test_セッションの外のPOSTは出さない(self):
        k = self.fmon()
        k.install()
        with self.assertRaises(kado.Tomeru):
            urllib.request.urlopen(self.post(), timeout=5)
        self.assertEqual(self.nise.kita, [])



def canary_card(**kae):
    """Canary（1回だけ・private の quarantine まで）の限定の承認に要る欄がそろったカード。本番の「取ってよい」にはならない。"""
    c = yoi_card(**{"承認対象の行為": ["Canary取得"], "商品用継続観測の可否": "未確認",
                    "個票の粒度": "未確認（列は取ってから見る）", "所在地の扱い": "未決（列を見てから）",
                    "公開時の粒度": "未決（public projection は列の分類のあと）", "publicに出す予定": "なし（Canary）",
                    "不確定事項": "取るまで未確認（転送・列・拒否状態）"})
    c.update(kae)
    return c


class 取得前の確かめの強め(Oki):
    """P1 robots.txt の途中切れ／P5 金庫の置き場（KINKO_PRIVATE の申告だけに頼らない）／
    P6 個人情報の欄の未決（語句が後ろに続いても）と、Canary の限定の状態（本番の「取ってよい」と分ける）。"""

    # ---- P1
    def test_robotsが途中で切れたら確かめられなかったとして止める(self):
        self.shounin()
        body = b"User-agent: *\nDisallow: /himitsu/\n"
        for cl in (str(len(body) + 50), "abc"):
            with self.subTest(cl=cl):
                kyou = self.p("data", "ref", "aite-kyou.json")
                if os.path.exists(kyou):
                    os.remove(kyou)
                k = self.mon({"https://%s/robots.txt" % HOST: (200, {"Content-Type": "text/plain", "Content-Length": cl}, body),
                              URL: HONBUN}, run_id="run-" + cl)
                with self.assertRaises(kado.Tomeru) as cm:
                    self.toru(k)
                self.assertIn("確かめられなかった", str(cm.exception))
                self.assertNotIn(URL, self.nise.kita)
        k = self.mon({"https://%s/robots.txt" % HOST: (200, {"Content-Type": "text/plain", "Content-Length": str(len(body))}, body),
                      URL: HONBUN}, run_id="run-ok")
        if os.path.exists(self.p("data", "ref", "aite-kyou.json")):
            os.remove(self.p("data", "ref", "aite-kyou.json"))
        self.assertEqual(self.toru(k), b"<html>ok</html>")                           # 大きさが合えば通る

    def test_kiretaは大きさの食い違いを返す(self):
        self.assertEqual(kado.kireta({"Content-Length": "10"}, 10), "")
        self.assertEqual(kado.kireta({}, 3), "")
        self.assertIn("途中で切れた", kado.kireta({"Content-Length": "10"}, 9))
        self.assertIn("読めない", kado.kireta({"Content-Length": "1e3"}, 9))

    # ---- P5
    def test_金庫がコードの置き場の中や_それを含む場所なら止める(self):
        naka = os.path.join(self.root, "_raw")
        os.makedirs(os.path.join(naka, "shita"))
        for d, kotoba in ((naka, "同じ git の作業木"), (os.path.join(naka, "shita"), "同じ git の作業木"),
                          (self.root, "そのもの"), (os.path.dirname(self.root), "そのもの")):
            with self.subTest(d=d):
                self.env = dict(self.env, KINKO_DIR=d)
                k = self.mon()
                riyuu = k.kinko_preflight(os.path.join(d, "raw"))
                self.assertTrue(any(kotoba in r for r in riyuu), riyuu)

    def test_コードの置き場がgitでなくても_その中のフォルダは金庫にしない(self):
        nashi = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, nashi, True)
        naka = os.path.join(nashi, "_raw")
        os.makedirs(naka)
        k = kado.Kado(nashi, REPO, UA, env=dict(self.env, KINKO_DIR=naka), transport=Nise({}))
        riyuu = k.kinko_preflight(os.path.join(naka, "raw"))
        self.assertTrue(any("別の git の置き場でないフォルダ" in r for r in riyuu), riyuu)

    def test_コードの置き場の下の_rawに別の置き場をcloneした形は通す(self):
        """いまの本番の置き方（大型店日報・競売・マップ：$GITHUB_WORKSPACE/_raw に private の金庫を clone）。"""
        git(self.root, "remote", "add", "origin", "https://github.com/VirgoB77/tameshi.git")
        raw = os.path.join(self.root, "_raw")
        os.makedirs(raw)
        git(raw, "init", "-q")
        for origin, yoi in (("https://github.com/VirgoB77/tameshi-raw.git", True),
                            ("https://github.com/VirgoB77/tameshi", False)):
            with self.subTest(origin=origin):
                subprocess.run(["git", "-C", raw, "remote", "remove", "origin"], capture_output=True)
                git(raw, "remote", "add", "origin", origin)
                self.env = dict(self.env, KINKO_DIR=raw)
                riyuu = self.mon().kinko_preflight(os.path.join(raw, "raw"))
                self.assertEqual(riyuu == [], yoi, riyuu)

    def test_コードの置き場のworktreeは金庫にしない(self):
        soto = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, soto, True)
        wt = os.path.join(soto, "wt")
        git(self.root, "worktree", "add", "-q", "-b", "kinko-no-tsumori", wt)
        self.addCleanup(subprocess.run, ["git", "-C", self.root, "worktree", "remove", "--force", wt], capture_output=True)
        self.env = dict(self.env, KINKO_DIR=wt)
        riyuu = self.mon().kinko_preflight(os.path.join(wt, "raw"))
        self.assertTrue(any("worktree" in r for r in riyuu), riyuu)

    def test_金庫が別のgitの置き場なら_取り込み先がコードと違うときだけよい(self):
        git(self.root, "remote", "add", "origin", "https://github.com/VirgoB77/tameshi.git")
        betsu = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, betsu, True)
        git(betsu, "init", "-q")
        for origin, jikkou, yoi in (("git@github.com:VirgoB77/tameshi-raw.git", "", True),
                                    ("git@github.com:VirgoB77/tameshi.git", "", False),        # コードと同じ置き場
                                    ("https://github.com/VirgoB77/tameshi-raw", "VirgoB77/tameshi-raw", False)):  # この実行の置き場
            with self.subTest(origin=origin, jikkou=jikkou):
                subprocess.run(["git", "-C", betsu, "remote", "remove", "origin"], capture_output=True)
                git(betsu, "remote", "add", "origin", origin)
                self.env = dict(self.env, KINKO_DIR=betsu, GITHUB_REPOSITORY=jikkou)
                riyuu = self.mon().kinko_preflight(os.path.join(betsu, "raw"))
                self.assertEqual(riyuu == [], yoi, riyuu)

    def test_金庫をsymlinkでコードの置き場の中へ向けても止める(self):
        """コードの置き場が git のとき（git 自身もリンクを解く）と、git でないとき（門が解かなければ通ってしまう）の2通り。"""
        nashi = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, nashi, True)
        for root in (self.root, nashi):
            with self.subTest(git=root == self.root):
                naka = os.path.join(root, "_raw")
                os.makedirs(naka)
                soto = os.path.join(tempfile.mkdtemp(), "kinko")
                self.addCleanup(shutil.rmtree, os.path.dirname(soto), True)
                try:
                    os.symlink(naka, soto, target_is_directory=True)
                except (OSError, NotImplementedError):
                    try:
                        import _winapi                          # Windows：symlink の権限が無くても junction は作れる
                        _winapi.CreateJunction(naka, soto)
                    except (ImportError, OSError):
                        self.skipTest("この環境では symlink も junction も作れない")
                env = dict(self.env, KINKO_DIR=soto)
                k = kado.Kado(root, REPO, UA, env=env, transport=Nise({}))
                riyuu = k.kinko_preflight(os.path.join(soto, "raw"))
                self.assertTrue(any("コードの置き場" in r for r in riyuu), riyuu)

    def test_金庫の置き場が違えば外へ出す前に止まる(self):
        self.shounin()
        self.env = dict(self.env, KINKO_DIR=self.root)
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    # ---- P6
    def test_個人情報の欄は語句が後ろに続いても未決(self):
        for k in kado.KOJIN_GATE_KISAI:
            for v in ("未確認（列を見ていない）", "未決（あとで決める）", "判断要：個人事業主の所在地", "分からない（まだ）", " 未確認"):
                with self.subTest(k=k, v=v):
                    self.assertEqual(kado.seishiki_jotai(yoi_card(**{k: v}), True), "規約未確定")
                    self.assertEqual(kado.seishiki_jotai(manual_card(**{k: v}), True), "規約未確定")     # manual も同じ
        self.assertEqual(kado.seishiki_jotai(yoi_card(), True), "取ってよい")
        self.assertEqual(kado.seishiki_jotai(manual_card(), True), "取ってよい")

    def test_Canaryの限定の状態は本番の取ってよいと分ける(self):
        c = canary_card()
        self.assertEqual(kado.seishiki_jotai(c, True), "規約未確定")                    # 本番にはならない
        self.assertEqual(kado.canary_jotai(c, True), "Canaryしてよい")
        self.assertEqual(kado.canary_jotai(c, False), "規約未確定")                     # 運営者承認が要る
        self.assertNotEqual(kado.canary_jotai(yoi_card(), True), "Canaryしてよい")      # 行為に Canary取得 が無い
        for kae in ({"publicに出す予定": "件数だけ"}, {"承認対象の行為": ["自動取得", "内部保存"]},
                    {"商品用継続観測の可否": "外す"}, {"privateに保存する予定": "未決"}, {"氏名を含みうる": ""},
                    {"STOP条件の確認": {"robots": "当たる：Disallow"}}, {"route種別": "manual"}):
            with self.subTest(kae=str(kae)[:40]):
                self.assertEqual(kado.canary_jotai(canary_card(**kae), True) == "Canaryしてよい", False)

    def test_Canary取得と本番の行為の両方があっても_本番の取得は本番の状態で見る(self):
        self.cards["tameshi-c"] = canary_card(承認対象の行為=["Canary取得", "自動取得", "内部保存"])
        self.kaku_all()
        self.shounin("tameshi-c")
        k = self.mon()
        self.assertEqual((k.seishiki("tameshi-c"), k.canary_jotai("tameshi-c")), ("規約未確定", "Canaryしてよい"))
        k.install()
        with self.assertRaises(kado.Tomeru) as cm:                                     # 本番の行為（自動取得・内部保存）
            with k.sesshon("tameshi-c", hozon_saki=os.path.join(self.kinko, "raw")):
                pass
        self.assertIn("規約未確定", " ".join(cm.exception.riyuu))
        self.assertEqual(self.nise.kita, [])

    def test_Canaryのカードは本番の取得に使えず_Canaryの取得にだけ使える(self):
        self.cards["tameshi-c"] = canary_card()
        self.kaku_all()
        self.shounin("tameshi-c")
        k = self.mon()
        self.assertEqual((k.seishiki("tameshi-c"), k.canary_jotai("tameshi-c")), ("規約未確定", "Canaryしてよい"))
        k.install()
        with self.assertRaises(kado.Tomeru):                                           # 本番の行為（自動取得・内部保存）
            with k.sesshon("tameshi-c", hozon_saki=os.path.join(self.kinko, "raw")):
                pass
        self.assertEqual(self.nise.kita, [])
        with k.sesshon("tameshi-c", koui=(kado.KOUI_CANARY,), hozon_saki=os.path.join(self.kinko, "raw")):
            with urllib.request.urlopen(URL, timeout=5) as r:
                self.assertEqual(r.read(), b"<html>ok</html>")
        row = {"price": 1, "demoto": k.demoto("tameshi-c", "2026-09-25T07:00:00+09:00", URL)}
        saki = os.path.join(self.kinko, "kansoku")
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi-c", [row], saki)                                   # 本番の保存にはならない
        self.assertTrue(k.kansoku_hozon("tameshi-c", [row], saki, canary=True))

T_MIRU = "2026-09-25T07:00:00+09:00"


def manual_card(**kae):
    """manual の route（人が普通のブラウザで見て、事実だけを書く）の、そろったカード。"""
    c = yoi_card(**{
        "route種別": "manual", "source_id": "tameshi-ken", "肯定根拠番号": "",
        "人の通常閲覧": "できる", "アクセス制御": "なし",
        "manual観測を禁じる文言・回答": "なし：規約と相手の回答（ロボット不可だけ）を読み、人の閲覧や記録の禁止は無かった",
        "記録件数上限": 3, "頻度": "1日1回",
    })
    c.update(kae)
    return c


class 独立監査の残り(Oki):
    """元の独立監査 Q1〜Q10 のうち、統合のあとで残っていたもの（2026-10-01）。"""

    def setUp(self):
        super().setUp()
        self.cards["tameshi-f"] = form_card()
        self.kaku_all()

    def dasu(self, k, cid, req):
        k.install()
        with k.sesshon(cid, hozon_saki=os.path.join(self.kinko, "raw")):
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.read()

    # ---- Q1 見出し
    def test_GETにCookieや認証や接続先の見出しを付けない(self):
        self.shounin()
        for h in ("Cookie", "Cookie2", "Authorization", "Proxy-Authorization", "Host"):
            for kakushi in (False, True):
                with self.subTest(h=h, unredirected=kakushi):
                    req = urllib.request.Request(URL)
                    (req.add_unredirected_header if kakushi else req.add_header)(h, "x=1")
                    with self.assertRaises(kado.Tomeru) as cm:
                        self.dasu(self.mon(), "tameshi", req)
                    self.assertIn("の見出しを付けない", str(cm.exception))
                    self.assertEqual(self.nise.kita, [])                                # robots.txt も取りに行かない

    def test_GETのRefererは対象hostのページだけ(self):
        self.shounin()
        with self.assertRaises(kado.Tomeru) as cm:
            self.dasu(self.mon(), "tameshi", urllib.request.Request(URL, headers={"Referer": "https://example.com/x"}))
        self.assertIn("Referer", str(cm.exception))
        self.assertEqual(self.nise.kita, [])
        req = urllib.request.Request(URL, headers={"Referer": "https://%s/data/" % HOST, "Accept-Language": "ja"})
        self.assertEqual(self.dasu(self.mon(), "tameshi", req), b"<html>ok</html>")    # 同じ host のページから来たと言うのはよい

    def test_POSTに付けてよい見出しは許す一覧だけ(self):
        self.shounin("tameshi-f")
        data = kado.form_nakami(form_card()["公開form"])
        for midashi in ({"X-Api-Key": "k"}, {"Origin": "https://%s" % HOST}, {"Cookie2": "a=1"}, {"Host": "evil.example"},
                        {"Content-Type": "multipart/form-data; boundary=x"}, {"Accept": "*/*"}):
            with self.subTest(midashi=midashi):
                k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: XLSX})
                with self.assertRaises(kado.Tomeru):
                    self.dasu(k, "tameshi-f", urllib.request.Request(ACTION, data=data, headers=midashi, method="POST"))
                self.assertEqual(self.nise.kita, [])
        req = urllib.request.Request(ACTION, data=data, method="POST")
        req.add_unredirected_header("Cookie", "a=1")                                    # 隠した見出しも見る
        with self.assertRaises(kado.Tomeru):
            self.dasu(self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: XLSX}), "tameshi-f", req)
        k = self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION: XLSX})
        req = urllib.request.Request(ACTION, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "x"})
        self.assertEqual(self.dasu(k, "tameshi-f", req), b"PK\x03\x04tameshi")       # 許す一覧の中ならよい

    # ---- Q2 門の外の道
    def test_fileやdataのURLも門で止める(self):
        self.shounin()
        for url in ("file:///etc/hosts", "data:text/plain,abc"):
            with self.subTest(url=url):
                with self.assertRaises(kado.Tomeru):
                    self.dasu(self.mon(), "tameshi", urllib.request.Request(url))
                urllib.request.install_opener(urllib.request.build_opener(kado._Tozasu()))    # 門を始める前も
                with self.assertRaises(kado.Tomeru):
                    urllib.request.urlopen(url, timeout=1)
        self.assertEqual(self.nise.kita, [])

    def test_get_methodを差し替えたRequestは出さない(self):
        self.shounin()

        class Kawaru(urllib.request.Request):
            n = 0

            def get_method(self):
                Kawaru.n += 1
                return "GET" if Kawaru.n == 1 else "POST"
        with self.assertRaises(kado.Tomeru) as cm:
            self.dasu(self.mon(), "tameshi", Kawaru(URL))
        self.assertIn("get_method", str(cm.exception))
        self.assertEqual(self.nise.kita, [])

    def test_urlopenにcontextやcafileを渡せない(self):
        import ssl
        self.shounin()
        k = self.mon()
        k.install()
        for kw in ({"context": ssl.create_default_context()}, {"cafile": "x.pem"}, {"capath": "."}):
            with self.subTest(kw=sorted(kw)):
                with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
                    with self.assertRaises(kado.Tomeru):
                        urllib.request.urlopen(URL, timeout=5, **kw)
        self.assertEqual(self.nise.kita, [])

    # ---- Q10（と Q2）import しただけで閉じることを、別のプロセスで確かめる
    def test_importしただけで門の無い通信は出ない_別のプロセス(self):
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from common import kado\nimport urllib.request\n"
                "try:\n    urllib.request.urlopen('https://example.invalid/', timeout=1)\n"
                "except kado.Tomeru:\n    print('TOMETA')\n" % HERE)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn("TOMETA", r.stdout, r.stderr[-500:])

    # ---- Q5 URL範囲
    def test_URL範囲は前方一致にしない(self):
        h = kado.han_ni_hairu
        a = "https://%s/resource/?id=21424" % HOST
        self.assertTrue(h(a, a))
        for url in (a + "0", a + "&x=1", "https://%s/resource/?id=2142" % HOST, "https://%s/resource/" % HOST):
            with self.subTest(url=url):
                self.assertFalse(h(url, a))
        f = "https://%s/data/list.html" % HOST
        self.assertTrue(h(f, f))
        for url in (f + "x", f + ".bak", f + "?x=1", f + "/../x", "https://%s/data/list%%2ehtml" % HOST):
            with self.subTest(url=url):
                self.assertFalse(h(url, f))
        d = "https://%s/data/" % HOST
        self.assertTrue(h("https://%s/data/a/b.csv" % HOST, d))
        for url in ("https://%s/database/x" % HOST, "https://%s/data\\..\\x" % HOST, "https://%s/data/%%2e%%2e/x" % HOST,
                    "https://%s/data/%%2fetc" % HOST, "http://%s/data/a" % HOST, "https://%s:8443/data/a" % HOST,
                    "https://u:p@%s/data/a" % HOST, "https://other.example/data/a"):
            with self.subTest(url=url):
                self.assertFalse(h(url, d))

    def test_公開formのactionも前方一致にしない(self):
        f = form_card()
        f["公開form"] = dict(f["公開form"], action=ACTION + "0")                         # URL範囲は ?id=21424 の1本
        self.assertIsNone(kado.koukai_form(f))
        self.assertIsNotNone(kado.koukai_form(form_card()))

    def test_URL範囲の外は門で止める(self):
        self.shounin("tameshi-f")                                                        # URL範囲は ?id=21424 の1本
        with self.assertRaises(kado.Tomeru) as cm:
            self.dasu(self.mon({"https://%s/robots.txt" % HOST: ROBOTS_OK, ACTION + "0": HONBUN}), "tameshi-f",
                      urllib.request.Request(ACTION + "0"))                              # ?id=214240
        self.assertIn("URL範囲の外", str(cm.exception))
        self.assertNotIn(ACTION + "0", self.nise.kita)

    # ---- Q7 運営者承認
    def test_承認日が今日より後なら認めない(self):
        self.shounin(承認日="2026-09-26")                                                # 今日は 2026-09-25
        ok, why = self.mon().shounin("tameshi")
        self.assertFalse(ok)
        self.assertIn("今日より後", why)
        self.shounin(承認日="2026-09-25")
        self.assertEqual(self.mon().shounin("tameshi"), (True, ""))

    def test_承認ファイルがsymlinkなら認めない(self):
        """承認ファイルそのものが symlink（作れる環境だけ）と、承認の置き場のフォルダが外への symlink・junction の2通り。"""
        self.shounin()
        oya = self.p("data", "ref", "shounin")
        p = os.path.join(oya, "tameshi.json")
        soto = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, soto, True)
        shutil.copy(p, os.path.join(soto, "tameshi.json"))
        os.remove(p)
        try:
            os.symlink(os.path.join(soto, "tameshi.json"), p)
        except (OSError, NotImplementedError):
            pass                                                                         # Windows：ファイルの symlink は作れない
        else:
            ok, why = self.mon().shounin("tameshi")
            self.assertFalse(ok)
            self.assertIn("symlink", why)
            os.remove(p)
        shutil.rmtree(oya)
        try:
            os.symlink(soto, oya, target_is_directory=True)
        except (OSError, NotImplementedError):
            try:
                import _winapi                                                           # Windows：junction は作れる
                _winapi.CreateJunction(soto, oya)
            except (ImportError, OSError):
                self.skipTest("この環境では symlink も junction も作れない")
        self.addCleanup(lambda: os.path.lexists(oya) and (os.unlink(oya) if os.path.islink(oya) else os.rmdir(oya)))
        ok, why = self.mon().shounin("tameshi")
        self.assertFalse(ok)
        self.assertIn("symlink", why)

    def test_取り込み先のmainと違う承認は使わない(self):
        self.shounin()
        moto = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, moto, True)
        bare = os.path.join(moto, "o.git")
        git(moto, "init", "-q", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        git(self.root, "push", "-q", "origin", "HEAD:refs/heads/main")
        git(self.root, "fetch", "-q", "origin")
        self.assertEqual(self.mon().shounin("tameshi"), (True, ""))                      # main と同じ承認
        self.shounin(承認日="2026-09-23")                                                # 別の branch で承認を書き換えた形
        ok, why = self.mon().shounin("tameshi")
        self.assertFalse(ok)
        self.assertIn("main と違う", why)

    # ---- Q9 今日の控えが壊れていたら
    def test_今日の控えが壊れていたら止めて上書きもしない(self):
        self.shounin()
        kyou = self.p("data", "ref", "aite-kyou.json")
        with open(kyou, "w", encoding="utf-8") as f:
            f.write("{こわれた")
        k = self.mon()
        self.assertTrue(any("壊れている" in r for r in k.card_mon("tameshi")))
        with self.assertRaises(kado.Tomeru):
            self.dasu(k, "tameshi", urllib.request.Request(URL))
        self.assertEqual(self.nise.kita, [])
        with open(kyou, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{こわれた")
        with self.assertRaises(kado.Tomeru):
            k._kyou_kaku("ためし県", saigo=1.0)
        os.remove(kyou)
        k = self.mon()
        k.install()
        with k.sesshon("tameshi", hozon_saki=os.path.join(self.kinko, "raw")):
            with open(kyou, "w", encoding="utf-8") as f:
                f.write("[")                                                            # 取りに行く途中で壊れた
            with self.assertRaises(kado.Tomeru) as cm:
                urllib.request.urlopen(URL, timeout=5)
        self.assertIn("壊れている", str(cm.exception))
        self.assertEqual(self.nise.kita, [])

    # ---- Q3 検査の穴
    def test_知らないrouteは取ってよいにならない(self):
        for route in ("xyz", "WEB", " web"):
            with self.subTest(route=route):
                self.assertNotEqual(kado.seishiki_jotai(yoi_card(route種別=route), True), "取ってよい")
                self.assertEqual(kado.METHOD_ROUTE.get(route, ()), ())


class manualの承認経路(Oki):
    """正本 3.4a。manual は機械の取得の門（sesshon）を通らないが、**門が無いのではない。**
    カードの manual の欄と、運営者承認（カード版・指紋）がそろったときだけ、人の観測を記録できる。"""

    def setUp(self):
        super().setUp()
        self.cards["tameshi-m"] = manual_card()
        self.kaku_all()
        self.saki = os.path.join(self.kinko, "manual")

    def kiroku(self, k, rows=None, url=URL):
        return k.manual_kiroku("tameshi-m", rows if rows is not None else [{"price": 100}],
                               T_MIRU, url, "運営者", self.saki)

    def test_承認と欄がそろえば記録できる_robotsは見ない_通信もしない(self):
        self.shounin("tameshi-m")
        # robots が全部を拒んでいても、manual の拒否ではない（robots は機械への指示）
        k = self.mon({"https://%s/robots.txt" % HOST: (200, {"Content-Type": "text/plain"},
                                                        b"User-agent: *\nDisallow: /\n")})
        self.assertEqual(k.manual_mon("tameshi-m"), [])
        path = self.kiroku(k)
        d = json.load(open(path, encoding="utf-8"))
        self.assertEqual(d["rows"][0]["demoto"]["route_kind"], "manual")
        self.assertEqual(d["rows"][0]["demoto"]["recorder_role"], "運営者")
        self.assertEqual(self.nise.kita, [])

    def test_運営者承認が無ければ記録できない(self):
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.kiroku(k)
        self.assertEqual(os.listdir(self.kinko), [])

    def test_manualの欄が1つでも欠ければ取ってよいにならない(self):
        for kae in ({"source_id": ""}, {"対象URL": ""}, {"人の通常閲覧": "分からない"}, {"アクセス制御": ""},
                    {"manual観測を禁じる文言・回答": "該当文言なし"}, {"manual観測を禁じる文言・回答": "なし："},
                    {"記録件数上限": 0}, {"記録件数上限": "3"}, {"頻度": "1日3回"}, {"商品用継続観測の可否": "読めない"},
                    {"氏名を含みうる": ""}, {"再確認期限": ""}):
            with self.subTest(kae=kae):
                self.assertEqual(kado.seishiki_jotai(manual_card(**kae), True), "規約未確定")
        self.assertEqual(kado.seishiki_jotai(manual_card(), True), "取ってよい")

    def test_人にもアクセス制御が出るならmanualにしない(self):
        for v in ("CAPTCHA あり", "ログインが要る", "403"):
            with self.subTest(v=v):
                self.assertEqual(kado.seishiki_jotai(manual_card(アクセス制御=v), True), "規約未確定")

    def test_件数の上限と_同じrouteは1日1回(self):
        self.shounin("tameshi-m")
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.kiroku(k, rows=[{"price": i} for i in range(4)])      # 上限3を超える
        self.kiroku(k)
        with self.assertRaises(kado.Tomeru):
            self.kiroku(self.mon(run_id="run-2"))                         # 同じ日の2回目

    def test_見たURLが対象の外なら記録しない(self):
        self.shounin("tameshi-m")
        with self.assertRaises(kado.Tomeru):
            self.kiroku(self.mon(), url="https://www.example.com/x")

    def test_manualのカードで機械の取得はしない(self):
        self.shounin("tameshi-m")
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            self.toru(k, cid="tameshi-m")
        self.assertEqual(self.nise.kita, [])


    # ---- 監査 B-02：頻度は、同じ相手（route をまたぐ）とカードの頻度で数える
    def kiroku_hi(self, hi, cid="tameshi-m", run=None):
        k = self.mon(today=hi, run_id=run or "run-" + hi)
        return k.manual_kiroku(cid, [{"price": 100}], "%sT10:00:00+09:00" % hi, URL, "運営者", self.saki)

    def hindo(self, v):
        self.cards["tameshi-m"] = manual_card(頻度=v)
        self.kaku_all()
        self.shounin("tameshi-m", 承認日="2026-08-01")

    def test_同じ相手は_別のrouteでも同じ日に2回記録しない(self):
        self.cards["tameshi-m2"] = manual_card()          # 同じ相手・別の route
        self.kaku_all()
        self.shounin("tameshi-m")
        self.shounin("tameshi-m2")
        self.kiroku_hi("2026-09-25")
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-09-25", cid="tameshi-m2", run="run-x")
        self.kiroku_hi("2026-09-26", cid="tameshi-m2")    # 次の日なら、別の route は記録できる

    # 週・月は、前の記録からの日数で数える（rolling window。暦の境目ではリセットしない。統括判断 2026-09-30）
    def test_週1回は_日曜から月曜でも7日未満なら止める(self):
        self.hindo("週1回")
        self.kiroku_hi("2026-09-27")                      # 日曜
        for hi in ("2026-09-28", "2026-10-03"):           # 次の日の月曜（暦の週は変わる）・6日後
            with self.subTest(hi=hi):
                with self.assertRaises(kado.Tomeru):
                    self.kiroku_hi(hi)
        self.kiroku_hi("2026-10-04")                      # 7日後

    def test_週1回は_6日差なら止め_7日差なら通す(self):
        self.hindo("週1回")
        self.kiroku_hi("2026-09-24")
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-09-30")                  # 9/24 → 9/30 は6日
        self.kiroku_hi("2026-10-01")                      # 7日

    def test_週1回は_7日差なら通る(self):
        self.hindo("週1回")
        self.kiroku_hi("2026-09-23")
        self.kiroku_hi("2026-09-30")                      # 9/23 → 9/30 は7日

    def test_月1回は_月末から月初でも30日未満なら止める(self):
        self.hindo("月1回")
        self.kiroku_hi("2026-09-30")
        for hi in ("2026-10-01", "2026-10-29"):           # 暦の月は変わる・29日後
            with self.subTest(hi=hi):
                with self.assertRaises(kado.Tomeru):
                    self.kiroku_hi(hi)

    def test_月1回は_30日差なら通る(self):
        self.hindo("月1回")
        self.kiroku_hi("2026-09-30")
        self.kiroku_hi("2026-10-30")                      # 30日後

    def test_月2回は_月をまたいでも直近30日に2件あれば3件目を止める(self):
        self.hindo("月2回")
        self.kiroku_hi("2026-09-25")
        self.kiroku_hi("2026-10-02")
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-10-05")                  # 月は変わったが、直近30日に2件

    def test_月2回は_30日の窓から古い記録が外れたら通る(self):
        self.hindo("月2回")
        self.kiroku_hi("2026-09-01")
        self.kiroku_hi("2026-09-10")
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-09-20")
        self.kiroku_hi("2026-10-01")                      # 9/1 は30日前で窓の外。窓の中は 9/10 の1件
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-10-02")                  # 窓の中に 9/10・10/1 の2件
        self.kiroku_hi("2026-10-10")                      # 9/10 が外れて、窓の中は 10/1 の1件

    def test_頻度を数える履歴が残る_読めなければ止める(self):
        self.shounin("tameshi-m")
        self.kiroku_hi("2026-09-25")
        d = json.load(open(self.p("data", "ref", "manual-rireki.json"), encoding="utf-8"))
        self.assertEqual([(r["hi"], r["aite"], r["route_id"]) for r in d["kiroku"]],
                         [("2026-09-25", "ためし県", "tameshi-m")])
        for kowareta in ("{", "[]", json.dumps({"schema": 1, "kiroku": [{"hi": "きのう", "aite": "ためし県",
                                                                          "route_id": "tameshi-m"}]}),
                         json.dumps({"schema": 1, "kiroku": [{"hi": "2026-12-01", "aite": "ためし県",
                                                              "route_id": "tameshi-m"}]})):   # 今日より後
            with self.subTest(kowareta=kowareta[:30]):
                with open(self.p("data", "ref", "manual-rireki.json"), "w", encoding="utf-8") as f:
                    f.write(kowareta)
                with self.assertRaises(kado.Tomeru):
                    self.kiroku_hi("2026-10-05")

    def rireki_kaku(self, *kiroku):
        with open(self.p("data", "ref", "manual-rireki.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "kiroku": [{"hi": hi, "aite": aite, "route_id": rid, "run": "run-x", "repo": REPO}
                                               for hi, aite, rid in kiroku]}, f, ensure_ascii=False)

    def test_今日より後の記録が1件でもあれば_相手もrouteも問わず止める(self):
        """manual-rireki.json は manual の頻度の判定全体の台帳。先の日は台帳そのものの時刻・破損を疑う条件。"""
        self.shounin("tameshi-m")
        for kiroku in ((("2026-12-01", "ためし県", "tameshi-m2"),),      # 同じ相手・別の route
                       (("2026-12-01", "ほかの県", "hoka-m"),),           # 別の相手
                       (("2026-10-04", "ほかの県", "hoka-m"),             # 正しい履歴にまじって1件だけ
                        ("2026-10-06", "ほかの県", "hoka-m2"))):
            with self.subTest(kiroku=kiroku):
                self.rireki_kaku(*kiroku)
                with self.assertRaises(kado.Tomeru):
                    self.kiroku_hi("2026-10-05")

    def test_今日より後の記録が無ければ_ほかの相手の履歴は頻度に効かない(self):
        self.shounin("tameshi-m")
        self.rireki_kaku(("2026-10-05", "ほかの県", "hoka-m"),                # 今日・別の相手
                         ("2026-10-04", "ほかの県", "hoka-m2"),
                         ("2026-09-20", "ほかの県", "hoka-m"))
        self.kiroku_hi("2026-10-05")
        d = json.load(open(self.p("data", "ref", "manual-rireki.json"), encoding="utf-8"))
        self.assertEqual(len(d["kiroku"]), 4)                               # 前の履歴は消さずに追記

    # ---- 独立再監査：manual-rireki の行は、保存した形のとおりかを全部見る
    def test_履歴の行の形が1つでも違えば止める(self):
        self.shounin("tameshi-m")
        yoi = {"hi": "2026-10-01", "aite": "ほかの県", "route_id": "hoka-m", "run": "run-x", "repo": REPO}
        for kae in ({"route_id": "tameshi-m\n"}, {"route_id": "../tameshi-m"}, {"route_id": "data/ref/tameshi-m"},
                    {"route_id": "Tameshi-M"}, {"route_id": 1},
                    {"run": None}, {"run": "  "}, {"run": 1}, {"repo": None}, {"repo": " "},
                    {"aite": "  "}, {"aite": None},
                    {"hi": "2026-02-30"}, {"hi": "2026-9-01"}, {"hi": "2026-10-01T00:00"}):
            with self.subTest(kae=kae):
                r = {k: v for k, v in dict(yoi, **kae).items() if v is not None}
                with open(self.p("data", "ref", "manual-rireki.json"), "w", encoding="utf-8") as f:
                    json.dump({"schema": 1, "kiroku": [yoi, r]}, f, ensure_ascii=False)
                with self.assertRaises(kado.Tomeru) as cm:
                    self.kiroku_hi("2026-10-05")
                self.assertIn("形の違う行", str(cm.exception))
        # 形のそろった行だけなら通る（止まったのは形のせい）
        self.rireki_kaku(("2026-10-01", "ほかの県", "hoka-m"))
        self.kiroku_hi("2026-10-05")

    def test_壊れたroute_idの履歴で頻度をすり抜けられない(self):
        """前の記録の route_id が "tameshi-m\\n" だと、形を見ない数え方では、その記録がこの route に数えられない。"""
        for hindo, mae, ima in (("週1回", ["2026-09-25"], "2026-09-26"),
                                ("月1回", ["2026-09-25"], "2026-10-05"),
                                ("月2回", ["2026-09-20", "2026-09-25"], "2026-10-05")):
            with self.subTest(hindo=hindo):
                self.hindo(hindo)
                with open(self.p("data", "ref", "manual-rireki.json"), "w", encoding="utf-8") as f:
                    json.dump({"schema": 1, "kiroku": [{"hi": hi, "aite": "ためし県", "route_id": "tameshi-m\n",
                                                        "run": "run-" + hi, "repo": REPO} for hi in mae]},
                              f, ensure_ascii=False)
                with self.assertRaises(kado.Tomeru) as cm:
                    self.kiroku_hi(ima)
                self.assertIn("形の違う行", str(cm.exception))
                self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))

    # ---- 独立再監査：URL に user・password（userinfo）があれば、host が正しくても通さない
    def test_userinfoつきのURLは_正しい対象hostでも通さない(self):
        for url in ("https://user@%s/data/list.html" % HOST, "https://user:pass@%s/data/list.html" % HOST,
                    "https://:pass@%s/data/list.html" % HOST, "https://@%s/data/list.html" % HOST):
            with self.subTest(url=url):
                # 共通の入口
                self.assertIsNone(kado._url_host(url))
                # manual のカードの対象URL（対象host は正しい [HOST]）
                self.assertEqual(kado.seishiki_jotai(manual_card(対象URL=url), True), "規約未確定")
                # 出どころ（manual も web も）
                m = {"source_id": "tameshi-ken", "route_id": "tameshi-m", "route_kind": "manual",
                     "observed_at": T_MIRU, "source_url": url, "recorder_role": "運営者"}
                self.assertIn("source_url", kado.demoto_tarinai({"demoto": m}))
                self.assertIn("source_url", kado.demoto_tarinai({"demoto": dict(m, route_kind="web", route_id="tameshi")}))
        # manual_kiroku の source_url（カードも承認も正しい。userinfo だけが理由で止まる）
        self.shounin("tameshi-m")
        for i, url in enumerate(("https://user@%s/data/list.html" % HOST, "https://user:pass@%s/data/list.html" % HOST)):
            with self.subTest(url=url):
                with self.assertRaises(kado.Tomeru):
                    self.kiroku(self.mon(run_id="run-u%d" % i), url=url)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))
        self.kiroku(self.mon(run_id="run-ok"), url=URL)                     # userinfo を外せば通る

    # ---- Production Readiness：manual の保存の順（先に履歴、あとで観測）
    def kansoku_no_kazu(self):
        if not os.path.isdir(self.saki):
            return 0
        return len([f for f in os.listdir(self.saki) if f.endswith(".json")])

    def rireki_no_kazu(self):
        pth = self.p("data", "ref", "manual-rireki.json")
        return len(json.load(open(pth, encoding="utf-8"))["kiroku"]) if os.path.exists(pth) else 0

    def test_保存の順_履歴を書けなければ観測も書かない_観測だけ残らない(self):
        self.shounin("tameshi-m")
        with mock.patch.object(kado, "_kaku", side_effect=OSError("履歴を書けない")):
            with self.assertRaises(OSError):
                self.kiroku_hi("2026-10-05")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (0, 0))
        # 再実行：何も残っていないので、同じ日にもう一度記録できる（二重にはならない）
        self.kiroku_hi("2026-10-05")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (1, 1))

    def test_保存の順_観測を書けなければ履歴だけ残り_止まる側に数える(self):
        self.shounin("tameshi-m")
        with mock.patch.object(kado.os, "link", side_effect=OSError("観測を書けない")):
            with self.assertRaises(OSError):
                self.kiroku_hi("2026-10-05")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (0, 1))
        self.assertEqual([f for f in os.listdir(self.saki) if f.startswith(".kansoku-")], [])   # 書きかけも残さない
        # 再実行：同じ日は、履歴だけ残った1件で止まる。履歴は二重にならない
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-10-05", run="run-again")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (0, 1))
        # 次の日（1日1回）は通る
        self.kiroku_hi("2026-10-06")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (1, 2))

    def test_保存の順_観測を書いている途中で止まっても_書きかけの観測は残らない(self):
        self.shounin("tameshi-m")
        with mock.patch.object(kado.os, "fsync", side_effect=OSError("書いている途中で止まった")):
            with self.assertRaises(OSError):
                self.kiroku_hi("2026-10-05")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (0, 1))
        self.assertEqual(os.listdir(self.saki), [])                        # 名前のある観測も一時ファイルも無い
        with self.assertRaises(kado.Tomeru):                              # 同じ日の再試行は止まる
            self.kiroku_hi("2026-10-05", run="run-again")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (0, 1))

    def test_保存の順_hard_linkのできない置き場では観測を保存しない(self):
        self.shounin("tameshi-m")
        with mock.patch.object(kado.os, "link", side_effect=PermissionError("この置き場は hard link できない")):
            with self.assertRaises(OSError):
                self.kiroku_hi("2026-10-05")
        self.assertEqual(os.listdir(self.saki), [])
        self.assertEqual(self.rireki_no_kazu(), 1)                         # 止まる側に数える

    def test_保存の順_書く前の確かめで止まるなら履歴も書かない(self):
        self.shounin("tameshi-m")
        k = self.mon(today="2026-10-05", run_id="run-x")
        with self.assertRaises(kado.Tomeru):                              # 金庫の外
            k.manual_kiroku("tameshi-m", [{"price": 1}], "2026-10-05T10:00:00+09:00", URL, "運営者",
                            self.p("data", "manual"))
        self.assertEqual(self.rireki_no_kazu(), 0)
        self.kiroku_hi("2026-10-05")                                        # 止まった分は数えない

    def test_保存の順_成功した記録は二重にならない(self):
        self.shounin("tameshi-m")
        self.kiroku_hi("2026-10-05")
        with self.assertRaises(kado.Tomeru):
            self.kiroku_hi("2026-10-05", run="run-again")
        self.assertEqual((self.kansoku_no_kazu(), self.rireki_no_kazu()), (1, 1))

    # ---- 監査 B-03：対象host は hostname の並び。照合は完全一致だけ
    def test_対象hostが文字列1本や形の違う並びなら正式状態にならない(self):
        for v in (HOST, "not-" + HOST, [], [""], [1], ["https://%s/" % HOST], ["%s/data" % HOST],
                  [HOST.upper()], [HOST + " "], [HOST + "."]):
            with self.subTest(v=v):
                self.assertEqual(kado.seishiki_jotai(manual_card(対象host=v), True), "規約未確定")
                # 機械の route でも同じ（同じ形の穴）
                self.assertEqual(kado.seishiki_jotai(yoi_card(対象host=v), True), "規約未確定")
        # 対象URL の host が並びに無いのも manual にしない
        self.assertEqual(kado.seishiki_jotai(manual_card(対象host=["hoka.example.jp"]), True), "規約未確定")

    def test_hostの部分一致では通さない(self):
        self.shounin("tameshi-m")
        k = self.mon()
        for url in ("https://x%s/a" % HOST, "https://%s.evil.example/a" % HOST, "https://%s/a" % HOST[4:],
                    "http://%s/a" % HOST, "https://user@%s.evil.example/a" % HOST):
            with self.subTest(url=url):
                with self.assertRaises(kado.Tomeru):
                    self.kiroku(k, url=url)
        # 監査で再現した形：対象host が文字列 "not-<host>"、見た URL が <host>。
        # 正式状態（manual_mon）を通ったことにしても、manual_kiroku が自分で形を見直して止める
        self.cards["tameshi-s"] = manual_card(対象host="not-" + HOST)
        self.kaku_all()
        self.shounin("tameshi-s")
        k = self.mon(run_id="run-s")
        k.manual_mon = lambda cid: []
        with self.assertRaises(kado.Tomeru):
            k.manual_kiroku("tameshi-s", [{"price": 1}], T_MIRU, URL, "運営者", self.saki)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))

    def test_正しい並びの完全一致だけ通る(self):
        self.cards["tameshi-m"] = manual_card(対象host=["hoka.example.jp", HOST])
        self.kaku_all()
        self.shounin("tameshi-m")
        path = self.kiroku(self.mon(), url="https://%s/data/x.html" % HOST.upper())   # URL の host は小文字にそろえて見る
        d = json.load(open(path, encoding="utf-8"))
        self.assertEqual(d["rows"][0]["demoto"]["route_id"], "tameshi-m")


class 本番の保存の境界(Oki):
    """正本 3.4a。**出どころ（source_id・route_id・route_kind・observed_at）の無い観測を、本番へ新しく保存させない。**
    観測を Kado.kansoku_hozon で保存する段だけが本番の取得に入れ、保存の境界で出どころを確かめる。"""

    def setUp(self):
        super().setUp()
        self.cards["tameshi"] = konkyo5_card()
        self.kaku_all()
        self.shounin()
        self.saki = os.path.join(self.kinko, "kansoku")

    def test_出どころつきで保存する段でなければ_本番の取得に入れない(self):
        k = self.mon(kansoku_demoto=False)
        with self.assertRaises(kado.Tomeru):
            self.toru(k)
        self.assertEqual(self.nise.kita, [])

    def test_出どころの無い観測は保存しない(self):
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi", [{"price": 1}], self.saki)
        d = dict(k.demoto("tameshi", T_MIRU))
        del d["observed_at"]
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi", [{"price": 1, "demoto": d}], self.saki)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))

    def test_1件でも欠けていれば_1件も保存しない(self):
        k = self.mon()
        ok = {"price": 1, "demoto": k.demoto("tameshi", T_MIRU)}
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi", [ok, {"price": 2}], self.saki)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))

    def test_ほかのrouteの出どころは保存しない(self):
        self.cards["tameshi-api"] = konkyo5_card(route種別="api", 肯定根拠番号="3")
        self.kaku_all()
        k = self.mon()
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi", [{"price": 1, "demoto": k.demoto("tameshi-api", T_MIRU)}], self.saki)

    def test_出どころつきなら金庫へ保存できる(self):
        k = self.mon()
        path = k.kansoku_hozon("tameshi", [{"price": 1, "demoto": k.demoto("tameshi", T_MIRU, URL)}], self.saki)
        d = json.load(open(path, encoding="utf-8"))
        self.assertEqual(d["rows"][0]["demoto"]["route_id"], "tameshi")
        self.assertEqual(d["rows"][0]["demoto"]["source_url"], URL)

    def test_金庫の外と_宣言の無い段と_承認の無いカードには保存しない(self):
        k = self.mon()
        row = {"price": 1, "demoto": k.demoto("tameshi", T_MIRU)}
        with self.assertRaises(kado.Tomeru):
            k.kansoku_hozon("tameshi", [row], self.p("data", "kansoku"))              # 金庫の外
        with self.assertRaises(kado.Tomeru):
            self.mon(kansoku_demoto=False).kansoku_hozon("tameshi", [row], self.saki)
        os.remove(self.p("data", "ref", "shounin", "tameshi.json"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "承認を消す")
        with self.assertRaises(kado.Tomeru):
            self.mon().kansoku_hozon("tameshi", [row], self.saki)

    # ---- 監査 B-01：保存の境界が、出どころの中身まで自分で見直す（demoto() を通したことを前提にしない）
    def test_壊れたobserved_atは保存の境界で止める(self):
        k = self.mon()
        for v in ("NOT-A-DATE", "2026-09-29", "2026-09-29T07:00:00", "", " ", 20260929, None):
            with self.subTest(v=v):
                d = dict(k.demoto("tameshi", T_MIRU), observed_at=v)
                with self.assertRaises(kado.Tomeru):
                    k.kansoku_hozon("tameshi", [{"price": 1, "demoto": d}], self.saki)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))

    def test_source_id_route_id_route_kindの形と整合を保存の境界で見る(self):
        k = self.mon()
        moto = k.demoto("tameshi", T_MIRU, URL)
        for kae in ({"source_id": "Tameshi Ken"}, {"source_id": "tameshi-ken\n"}, {"source_id": 1},
                    {"source_id": "hoka-ken"},                       # 形は正しいが、カードと違う
                    {"route_id": "../tameshi"}, {"route_id": "tameshi\n"}, {"route_id": ["tameshi"]},
                    {"route_id": "tameshi-2"},                       # 形は正しいが、保存する route と違う
                    {"route_kind": "ftp"}, {"route_kind": "WEB"}, {"route_kind": ["web"]},
                    {"route_kind": "api"},                           # 形は正しいが、カードと違う
                    {"source_url": "not-a-url"}, {"source_url": "ftp://%s/x" % HOST}, {"source_url": ""}):
            with self.subTest(kae=kae):
                with self.assertRaises(kado.Tomeru):
                    k.kansoku_hozon("tameshi", [{"price": 1, "demoto": dict(moto, **kae)}], self.saki)
        self.assertFalse(os.path.exists(self.saki) and os.listdir(self.saki))
        # 壊していない行は保存できる（止まったのは中身のせい）
        self.assertTrue(k.kansoku_hozon("tameshi", [{"price": 1, "demoto": moto}], self.saki))

    def test_出どころの形は_カードと照らさなくても見る(self):
        """保存の境界の demoto_tarinai は、カードと照らす前に、形の違いを自分で返す（商品へ絞る側も同じ関数）。"""
        moto = {"source_id": "tameshi-ken", "route_id": "tameshi", "route_kind": "web", "observed_at": T_MIRU}
        self.assertEqual(kado.demoto_tarinai({"demoto": moto}), [])
        for kae, ke in (({"source_id": "Tameshi Ken"}, "source_id"), ({"source_id": "tameshi-ken\n"}, "source_id"),
                        ({"source_id": 1}, "source_id"), ({"route_id": "../tameshi"}, "route_id"),
                        ({"route_id": "tameshi\n"}, "route_id"), ({"route_id": ["tameshi"]}, "route_id"),
                        ({"route_kind": "ftp"}, "route_kind"), ({"route_kind": "WEB"}, "route_kind"),
                        ({"route_kind": ["web"]}, "route_kind")):
            with self.subTest(kae=kae):
                self.assertIn(ke, kado.demoto_tarinai({"demoto": dict(moto, **kae)}))
        # カードの source_id も、末尾の改行を通さない
        self.assertEqual(kado.seishiki_jotai(yoi_card(source_id="tameshi-ken\n"), True), "規約未確定")

    def test_manualの出どころも中身まで見る(self):
        m = {"source_id": "tameshi-ken", "route_id": "tameshi-m", "route_kind": "manual",
             "observed_at": T_MIRU, "source_url": URL, "recorder_role": "運営者"}
        self.assertEqual(kado.demoto_tarinai({"demoto": m}), [])
        for kae, ke in (({"recorder_role": "  "}, "recorder_role"), ({"recorder_role": 1}, "recorder_role"),
                        ({"source_url": None}, "source_url"), ({"source_url": "example.com/x"}, "source_url"),
                        ({"observed_at": "NOT-A-DATE"}, "observed_at")):
            with self.subTest(kae=kae):
                self.assertIn(ke, kado.demoto_tarinai({"demoto": dict(m, **kae)}))

    def test_機械の観測も_書いている途中で止まれば何も残らない(self):
        k = self.mon()
        row = {"price": 1, "demoto": k.demoto("tameshi", T_MIRU)}
        for err in (mock.patch.object(kado.os, "fsync", side_effect=OSError("書いている途中で止まった")),
                    mock.patch.object(kado.os, "link", side_effect=PermissionError("hard link できない"))):
            with self.subTest(err=err.attribute):
                with err:
                    with self.assertRaises(OSError):
                        k.kansoku_hozon("tameshi", [row], self.saki)
                self.assertEqual(os.listdir(self.saki), [])

    def test_観測は書き終えてから名前を付ける(self):
        """名前を付けるところで止まっても、名前のある観測も、書きかけの一時ファイルも残らない。"""
        k = self.mon()
        row = {"price": 1, "demoto": k.demoto("tameshi", T_MIRU)}
        with mock.patch.object(kado.os, "link", side_effect=OSError("名前を付けられない")):
            with self.assertRaises(OSError):
                k.kansoku_hozon("tameshi", [row], self.saki)
        self.assertEqual(os.listdir(self.saki), [])
        path = k.kansoku_hozon("tameshi", [row], self.saki)
        self.assertEqual(os.listdir(self.saki), [os.path.basename(path)])
        self.assertEqual(json.load(open(path, encoding="utf-8"))["rows"], [row])

    def test_出どころつきで保存すると名乗った段は_kansoku_hozonを呼ぶ(self):
        """置き場の .py を歩いて見る（名前の一覧で決め打ちしない）。検査と common は外す。"""
        ne = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mita, warui = 0, []
        for d0, dirs, files in os.walk(ne):
            dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d not in ("tests", "common", "data")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                mita += 1
                t = open(os.path.join(d0, f), encoding="utf-8", errors="replace").read()
                if "kansoku_demoto=True" in t and ".kansoku_hozon(" not in t:
                    warui.append(os.path.relpath(os.path.join(d0, f), ne))
        self.assertGreater(mita, 0, "置き場の .py を1本も見ていない（拾い方が壊れている）")
        self.assertEqual(warui, [], "出どころつきで保存すると名乗ったのに、kansoku_hozon を呼んでいない")


if __name__ == "__main__":
    unittest.main()
