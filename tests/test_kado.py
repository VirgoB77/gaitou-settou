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

    def test_個人情報と個票の取得前ゲートが未決なら取ってよいにならない(self):
        """民間公開Web v3 §6。9つのどれかが未決なら、取得を始めない。"""
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
        """4（行政等の公式な再利用条件）は民間には使えない（民間公開Web v3 §3 は3つに閉じている）。"""
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
        self.assertEqual(kado.seishiki_jotai(yoi_card(肯定根拠番号="5"), True), "規約未確定")

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
        k = kado.Kado(self.root, REPO, UA, env=self.env, transport=self.nise, run_id="run-h",
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
        k = kado.Kado(self.root, REPO, UA, env=self.env, transport=Nise({
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
        k = kado.Kado(self.root, REPO, UA, env=self.env, transport=nise, run_id="run-1",
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
        なり、この検査が鳴る**（止まらずに回り続けて、検査そのものが終わらない、を避ける）。
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


if __name__ == "__main__":
    unittest.main()
