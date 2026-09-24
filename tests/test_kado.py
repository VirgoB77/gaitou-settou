"""取りに行く前の門（common/kado.py）と、完全観測（common/kanzen.py）の検査。

**本物の相手には1本も出さない。** 通信は偽の相手（Nise）が受ける。
承認の出どころは、一時フォルダに作った git の置き場で確かめる。

    python3 -m unittest tests.test_kado
"""
import contextlib
import email.message
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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


class Nise(urllib.request.BaseHandler):
    """偽の相手。URL ごとに (status, headers, body) を返し、来た URL を全部控える。"""
    handler_order = 50

    def __init__(self, kotae):
        self.kotae = kotae
        self.kita = []
        self.nanori = []           # 来た要求の名乗り（User-Agent）

    def _open(self, req):
        self.kita.append(req.full_url)
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
        "個票の粒度": "未確認", "所在地の扱い": "未確認",
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
        self.daicho = {"aite": {"ためし県": {"host": [HOST], "担当": REPO}}}
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

    def shounin(self, cid="tameshi", ai=False, card=None, **kae):
        card = card or self.cards[cid]
        s = {"カード": cid, "運営者承認": "承認", "承認したカード版": card["カード版"],
             "承認時カード指紋": kado.card_shimon(card), "承認日": "2026-09-24"}
        s.update(kae)
        self.kaku("data/ref/shounin/%s.json" % cid, s)
        git(self.root, "add", "-A")
        msg = "承認"
        if ai:
            msg += "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
        git(self.root, "commit", "-q", "-m", msg)

    def mon(self, kotae=None, **kw):
        self.nise = Nise(kotae if kotae is not None else {
            "https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN})
        self.naps = []
        return kado.Kado(self.root, REPO, UA, env=self.env, transport=self.nise,
                         run_id=kw.pop("run_id", "run-1"), sleep=self.naps.append,
                         now=kw.pop("now", lambda: 1000.0), **kw)

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

    def test_AIが書いた承認は数えない(self):
        self.shounin(ai=True)
        ok, why = self.mon().shounin("tameshi")
        self.assertFalse(ok)
        self.assertIn("AI", why)

    def test_自動実行が書いた承認は数えない(self):
        self.shounin()
        with open(self.p("data/ref/shounin/tameshi.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        git(self.root, "commit", "-qam", "自動",
            env={"GIT_AUTHOR_NAME": "github-actions[bot]",
                 "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"})
        self.assertFalse(self.mon().shounin("tameshi")[0])

    def test_保存していない承認は数えない(self):
        self.shounin()
        s = json.load(open(self.p("data/ref/shounin/tameshi.json"), encoding="utf-8"))
        s["承認日"] = "2026-09-25"
        self.kaku("data/ref/shounin/tameshi.json", s)
        self.assertFalse(self.mon().shounin("tameshi")[0])

    def test_浅い履歴では承認を数えない(self):
        self.shounin()
        with open(self.p(".git", "shallow"), "w") as f:
            f.write(subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout)
        self.assertFalse(self.mon().shounin("tameshi")[0])

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
        self.toru(self.mon(run_id="run-1", now=lambda: 1000.0))
        k2 = self.mon(run_id="run-1", now=lambda: 1001.0)
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
        k = kado.Kado(self.root, REPO, UA, env=self.env, transport=self.nise, run_id="run-h",
                      sleep=lambda s: None, now=lambda: 1000.0, robots_hikae=hikae)
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
        k = kado.Kado(self.root, REPO, UA, env=self.env, transport=Nise({
            "https://%s/robots.txt" % HOST: ROBOTS_OK, URL: HONBUN}), run_id="run-s",
            sleep=lambda s: None, now=lambda: 1000.0, robots_hikae=soto)
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


class import_しただけで閉じる(unittest.TestCase):

    def test_門を始めていない通信は出ない(self):
        urllib.request.install_opener(urllib.request.build_opener(kado._Tozasu()))
        with self.assertRaises(kado.Tomeru):
            urllib.request.urlopen("https://example.invalid/", timeout=1)


if __name__ == "__main__":
    unittest.main()
