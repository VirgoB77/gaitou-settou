"""取りに行く前の門。**この門を通らない通信は、1本も出さない。**

自動取得・スクレイピング運用基準 v1（統合案 rev2・rev3 の作業案）と、
各サイトの固定線を、取得の入口1か所で守る。

    カード      取得元ごとの判定カード（data/ref/torimoto-card.json）
    承認        運営者の承認（data/ref/shounin/<カード>.json）。1カード1ファイル
    機械札      機械が付ける停止の札（data/ref/kikai-fuda.json）。4語とは別
    相手台帳    相手・host・担当の置き場（data/ref/aite-daicho.json）
    今日の控え  相手ごとの「今日もう見たか」（data/ref/aite-kyou.json）

## 正式状態は入力しない。導出する

カードの「統括判定案」と、運営者の承認と、承認したカード版・カード指紋から決める。
**AI や取得プログラムが承認欄を書いても、承認にならない。**
承認ファイルを入れた commit に AI の印（Co-Authored-By: Claude 等）があるか、
自動実行（bot）が入れたものなら、その承認は数えない。
履歴が浅い（shallow）・保存されていない・書き換え途中なら、確かめられないので数えない。

**限界**：同じ GitHub の鍵で動いているので、印を付けずに commit された承認は
機械では見分けられない。ここで止められるのは、ふだんの手順で AI・機械が
書いてしまった承認まで。

## 通信の入口

`Kado.install()` が urllib の既定の opener を差し替える。以後の
`urllib.request.urlopen()` は全部この門の見張りを通る。

    セッションの外の通信          止める
    承認された URL 範囲の外       止める
    相手台帳で担当でない相手      止める
    robots が「通す」でない        止める（robots.txt は相手ごとに、この回で1度だけ見る）
    同じ相手に 429・503・401・403  その回は、その相手への残りを全部止める
    転送（redirect）              転送先ごとに、上を全部やり直す

**機械は4語を書き換えない。** 書くのは機械札と今日の控えだけ。
"""
import contextlib
import datetime
import hashlib
import json
import os
import re
import string
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

YON = ("未確認", "規約未確定", "取ってはいけない", "取ってよい")
FUDA = ("なし", "再読要", "停止要請", "使用停止", "不整合",
        "混雑継続", "拒否継続", "引用不一致", "指紋不安定", "退役")
KOJIN = ("はい", "いいえ", "分からない")
SHUBETSU = ("行政", "準公的", "民間一次", "二次・集約")
SEIKI = ("未調査", "無し", "有り・採用", "有り・不採用", "問い合わせ中")
SENMONKA_TOMERU = "取得開始前に必要"
KONKYO = ("1", "2", "3", "4")
ROBOTS = ("通す", "拒否", "混んでいる", "確かめられなかった")
# 1回の取得で、承認に含まれていなければならない行為。**取ったものは必ず金庫に残す**ので、
# 取るだけで内部保存もしている
KOUI_TORU = ("自動取得", "内部保存")
MATSU = 5                      # 同じ相手に続けて出すときに空ける秒数（固定線）
TIMEOUT = 40
ROBOTS_OOKISA = 512000         # これより大きい robots.txt は読みきれないので「確かめられなかった」
KONDA = (429, 503)
KOTOWARI = (401, 403)

CARD = os.path.join("data", "ref", "torimoto-card.json")
SHOUNIN = os.path.join("data", "ref", "shounin")
FUDA_PATH = os.path.join("data", "ref", "kikai-fuda.json")
DAICHO = os.path.join("data", "ref", "aite-daicho.json")
KYOU = os.path.join("data", "ref", "aite-kyou.json")

# AI が作った commit の印。**名前ではなく印で見る**
AI_NO_SHIRUSHI = re.compile(
    r"co-authored-by:\s*claude|generated with \[claude code\]|noreply@anthropic\.com"
    r"|claude\.com/claude-code", re.I)
BOT_NO_SHIRUSHI = re.compile(r"\[bot\]|github-actions", re.I)
# 「該当文言なし」だけを理由にした判定。**言及が無いことは許可ではない**
GAITOU_NASHI = re.compile(
    r"^\s*(該当(する)?(文言|記載|条項)(は|が)?(なし|無し|ない|見当たらない|確認できなかった)"
    r"|禁止(は|が)?(書いて|書かれて)(い)?ない|記載(は)?(なし|無し|ない)|特になし)\s*[。．.]?\s*$")


class Tomeru(urllib.error.URLError):
    """門で止めた。**通信は出ていない。**

    URLError の仲間にしてあるので、取得の失敗として扱う既存のコードは、
    そのまま「その1本は取れなかった」として次へ進む。
    """

    def __init__(self, riyuu):
        self.riyuu = list(riyuu) if isinstance(riyuu, (list, tuple)) else [riyuu]
        super().__init__("門で止めた：" + "／".join(self.riyuu))


# ------------------------------------------------------------------ 読み書き
def _yomu(path, kara):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return kara
    except (ValueError, OSError):
        return None                 # 壊れている。呼ぶ側で止める


def _kaku(path, d):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def card_shimon(card):
    """カード指紋。カードの中身を並べ直して取った SHA-256。"""
    s = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def jst_kyou(env=None):
    env = os.environ if env is None else env
    d = env.get("RUN_DATE") or ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(jst).date().isoformat()


def _hi(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------ 承認の出どころ
def _git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


def shounin_dedokoro(root, relpath):
    """承認ファイルが、運営者の手で入ったものか。**確かめられなければ数えない。**

    返り値は (よい, 理由)。
    """
    try:
        r = _git(root, "rev-parse", "--is-shallow-repository")
    except (OSError, subprocess.SubprocessError):
        return False, "git が使えない。承認の出どころを確かめられない"
    if r.returncode != 0:
        return False, "git の置き場ではない。承認の出どころを確かめられない"
    if r.stdout.strip() != "false":
        return False, "履歴が浅い（shallow）。承認の出どころを確かめられない"
    if _git(root, "ls-files", "--error-unmatch", relpath).returncode != 0:
        return False, "承認ファイルが保存（commit）されていない"
    if _git(root, "diff", "--quiet", "HEAD", "--", relpath).returncode != 0:
        return False, "承認ファイルが、保存したあとで書き換えられている"
    r = _git(root, "log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e", "--", relpath)
    kiroku = [x for x in r.stdout.split("\x1e") if x.strip()]
    if r.returncode != 0 or not kiroku:
        return False, "承認ファイルの履歴が読めない"
    for k in kiroku:
        h, an, ae, cn, ce, body = (k.strip("\n").split("\x1f") + [""] * 6)[:6]
        if AI_NO_SHIRUSHI.search(body) or AI_NO_SHIRUSHI.search(ae) or AI_NO_SHIRUSHI.search(ce):
            return False, "承認ファイルに AI の commit（%s）が入っている" % h[:7]
        if BOT_NO_SHIRUSHI.search(an + ae + cn + ce):
            return False, "承認ファイルに自動実行の commit（%s）が入っている" % h[:7]
    return True, ""


# ------------------------------------------------------------------ 正式状態
def _kaketeiru(card):
    """「取ってよい」に要る欄のうち、空いているもの。"""
    nai = []
    shoseki = card.get("規約証跡") or {}
    hoho = card.get("承認する取得方法") or {}
    if not _hi(card.get("規約確認日")):
        nai.append("規約確認日")
    if not (shoseki.get("規約URL") or "").startswith("http"):
        nai.append("規約URL")
    if str(card.get("肯定根拠番号") or "") not in KONKYO:
        nai.append("肯定根拠番号")
    riyuu = card.get("判定理由") or ""
    if not riyuu.strip() or GAITOU_NASHI.match(riyuu):
        nai.append("判定理由（「該当文言なし」だけでは足りない）")
    if not (card.get("重要な原文") or "").strip():
        nai.append("重要な原文")
    if not card.get("承認対象の行為"):
        nai.append("承認対象の行為")
    if not hoho.get("URL範囲"):
        nai.append("承認する取得方法の URL範囲")
    if not _hi(card.get("再確認期限")):
        nai.append("再確認期限")
    if card.get("source種別") not in SHUBETSU:
        nai.append("source種別")
    if card.get("個人情報を含みうる") not in KOJIN:
        nai.append("個人情報を含みうる")
    if card.get("不確定事項") not in ("なし",):
        nai.append("不確定事項が「なし」でない")
    return nai


def seishiki_jotai(card, shounin_yoi):
    """正式状態（4語）。**入力欄ではなく、ここで導出する。**

    shounin_yoi は、現在のカード版・カード指紋に対応した運営者承認があり、
    その出どころも確かめられたときだけ True。
    """
    if not isinstance(card, dict):
        return "未確認"
    an = card.get("統括判定案") or ""
    moto = (card.get("移行元") or {}).get("旧値") or ""
    if an not in YON or an == "未確認":
        # **既存の「取ってはいけない」「規約未確定」を未確認へ戻さない**
        return moto if moto in ("取ってはいけない", "規約未確定") else "未確認"
    if an in ("取ってはいけない", "規約未確定"):
        return an
    # 統括判定案 = 取ってよい
    if not shounin_yoi:
        return "規約未確定"
    if card.get("専門家確認") == SENMONKA_TOMERU:
        return "規約未確定"
    if _kaketeiru(card):
        return "規約未確定"
    return "取ってよい"


# ------------------------------------------------------------------ robots
_MIKAIHO = set(string.ascii_letters + string.digits + "-._~")


def _seiki(s):
    """RFC 9309 2.2.2 の形にそろえる。未予約文字の %XX はほどき、ASCII の外は %XX にする。"""
    b = s.encode("utf-8")
    out, i = [], 0
    while i < len(b):
        c = b[i]
        if c == 0x25 and re.fullmatch(rb"[0-9A-Fa-f]{2}", b[i + 1:i + 3]):
            v = int(b[i + 1:i + 3], 16)
            out.append(chr(v) if chr(v) in _MIKAIHO else "%%%02X" % v)
            i += 3
            continue
        if c < 0x21 or c >= 0x7F:
            out.append("%%%02X" % c)
        else:
            out.append(chr(c))
        i += 1
    return "".join(out)


def robots_kaiseki(text):
    """robots.txt を読む。返り値は (グループの並び, 読めた行の数)。

    グループは {"agents": [...], "rules": [(許可か, 型), ...], "delay": 秒か None}。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    groups, cur, agent_tsuzuki, yometa = [], None, False, 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            yometa += 1
            if cur is None or not agent_tsuzuki:
                cur = {"agents": [], "rules": [], "delay": None}
                groups.append(cur)
            cur["agents"].append(v.lower())
            agent_tsuzuki = True
            continue
        if k in ("allow", "disallow"):
            yometa += 1
            if cur is not None and v:          # **空の Disallow は、何も止めない**
                cur["rules"].append((k == "allow", v))
        elif k == "crawl-delay":
            yometa += 1
            if cur is not None:
                try:
                    cur["delay"] = max(0.0, float(v))
                except ValueError:
                    pass
        elif k == "sitemap":
            yometa += 1
        agent_tsuzuki = False
    return groups, yometa


def robots_group(groups, tokens):
    """自分に効くグループをまとめる。名指しが無ければ `*`。"""
    tokens = {t.lower() for t in tokens}
    mine = [g for g in groups if any(a.split("/")[0].strip() in tokens for a in g["agents"])]
    if not mine:
        mine = [g for g in groups if "*" in g["agents"]]
    rules, delay = [], None
    for g in mine:
        rules += g["rules"]
        if g["delay"] is not None:
            delay = max(delay or 0.0, g["delay"])
    return rules, delay


def _kata(pattern):
    p = _seiki(pattern)
    owari = p.endswith("$")
    if owari:
        p = p[:-1]
    rx = ".*".join(re.escape(x) for x in p.split("*"))
    return re.compile(rx + ("$" if owari else "")), len(pattern)


def robots_yurusu(rules, url):
    """その URL が許されているか。**最長一致。同じ長さなら Allow。**"""
    u = urllib.parse.urlsplit(url)
    path = u.path or "/"
    if path == "/robots.txt":
        return True
    target = _seiki(path + ("?" + u.query if u.query else ""))
    best = None                      # (長さ, 許可か)
    for allow, pattern in rules:
        rx, n = _kata(pattern)
        if rx.match(target):
            if best is None or n > best[0] or (n == best[0] and allow and not best[1]):
                best = (n, allow)
    return True if best is None else best[1]


def robots_hantei(status, ctype, body):
    """robots.txt の応答から、4状態のどれかを決める。**分からないものは通さない。**

    返り値は (状態, 規則 or None, 理由)。
    """
    if status in (404, 410):
        return "通す", [], "robots.txt が HTTP %s（置いていない）" % status
    if status in KONDA:
        return "混んでいる", None, "robots.txt が HTTP %s（混んでいる）" % status
    if status is None or not (200 <= status < 300):
        return "確かめられなかった", None, "robots.txt が HTTP %s" % status
    ct = (ctype or "").split(";")[0].strip().lower()
    if ct and ct != "text/plain":
        return "確かめられなかった", None, "robots.txt が text/plain で返らなかった（%s）" % ct
    body = body or b""
    if len(body) > ROBOTS_OOKISA:
        return "確かめられなかった", None, "robots.txt が大きすぎて読みきれない"
    atama = body.lstrip(b"\xef\xbb\xbf").lstrip()[:512].lower()
    if atama.startswith(b"<") or b"<html" in atama or b"<!doctype" in atama:
        return "確かめられなかった", None, "robots.txt のはずが HTML"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "確かめられなかった", None, "robots.txt が UTF-8 で読めない"
    groups, yometa = robots_kaiseki(text)
    if yometa == 0:
        # 空のファイルもここに来る。**200 が返っただけでは許可にしない**
        return "確かめられなかった", None, "robots.txt として読める行が1つも無い（空を含む）"
    return "通す", groups, "robots.txt を読んだ"


# ------------------------------------------------------------------ 門
class _TenSouShinai(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                    # robots.txt の転送は辿らない（確かめられなかった）


class Kado:
    """1回の実行に1つ。"""

    def __init__(self, root, repo, ua, *, ua_tokens=None, today=None, run_id=None,
                 env=None, transport=None, sleep=time.sleep, now=time.time):
        self.root = root
        self.repo = repo
        self.ua = ua
        self.tokens = set(ua_tokens or [ua.split("(")[0].strip().lower(),
                                        ua.split()[0].lower()])
        self.env = os.environ if env is None else env
        self.today = today or jst_kyou(self.env)
        self.run_id = run_id or self.env.get("GITHUB_RUN_ID") or "local-%d" % os.getpid()
        self.transport = transport
        self._sleep, self._now = sleep, now
        self._lock = threading.RLock()
        self._genzai = None            # いまのセッション (カードid, カード, 行為)
        self._robots = {}              # (scheme, host) -> (状態, 規則, delay, 理由)
        self._saigo = {}               # 相手 -> 最後に出した時刻
        self._tomatta = {}             # 相手 -> 理由（この回はもう行かない）
        self.kiroku = []               # 出した・止めた記録（報告用）
        cards = _yomu(self._p(CARD), {"cards": {}})
        self.cards = (cards or {}).get("cards") if isinstance(cards, dict) else None
        self.daicho = _yomu(self._p(DAICHO), None)

    def _p(self, rel):
        return os.path.join(self.root, rel)

    # ---------------------------------------------------------- カード
    def card(self, cid):
        if not isinstance(self.cards, dict):
            return None
        c = self.cards.get(cid)
        return c if isinstance(c, dict) else None

    def shounin(self, cid):
        """(よい, 理由)。現在のカード版と指紋に対応しているかまで見る。"""
        card = self.card(cid)
        if card is None:
            return False, "カードが無い"
        rel = os.path.join(SHOUNIN, "%s.json" % cid)
        s = _yomu(self._p(rel), {})
        if not s:
            return False, "運営者承認が無い"
        if s.get("運営者承認") != "承認":
            return False, "運営者承認が「承認」でない"
        if s.get("カード") != cid:
            return False, "承認ファイルのカード名が違う"
        if str(s.get("承認したカード版")) != str(card.get("カード版")):
            return False, "承認したカード版が、いまのカード版と違う"
        if s.get("承認時カード指紋") != card_shimon(card):
            return False, "承認時のカード指紋が、いまのカードと違う（承認のあとでカードが変わった）"
        if not _hi(s.get("承認日")):
            return False, "承認日が無い"
        return shounin_dedokoro(self.root, rel.replace(os.sep, "/"))

    def seishiki(self, cid):
        card = self.card(cid)
        ok = False
        if card is not None and card.get("統括判定案") == "取ってよい":
            ok, _ = self.shounin(cid)
        return seishiki_jotai(card, ok)

    # ---------------------------------------------------------- 機械札・控え
    def fuda(self, cid):
        d = _yomu(self._p(FUDA_PATH), {})
        if d is None:
            return "不整合", "機械札の置き場が壊れている"
        f = (d.get(cid) or {})
        v = f.get("札", "なし") if isinstance(f, dict) else "不整合"
        if v not in FUDA:
            return "不整合", "知らない札（%s）" % v
        return v, f.get("理由", "") if isinstance(f, dict) else ""

    def fuda_wo_tsukeru(self, cid, fuda, riyuu):
        """機械札を付ける。**外すことはしない**（外すのは統括の確認と運営者承認のあと）。"""
        if fuda not in FUDA or fuda == "なし":
            raise ValueError("機械が付けられる札ではない：%s" % fuda)
        path = self._p(FUDA_PATH)
        with self._lock:
            d = _yomu(path, {})
            if d is None:
                return
            if (d.get(cid) or {}).get("札", "なし") not in ("なし", None):
                return                  # もう付いている。上書きしない
            d[cid] = {"札": fuda, "理由": riyuu, "付けた日": self.today, "付けたもの": self.repo}
            _kaku(path, d)

    def _kyou_yomu(self):
        return _yomu(self._p(KYOU), {})

    def _kyou_kaku(self, aite, **kw):
        """今日の控えを書く。前の観測日の分は `mae` に1つだけ残す（混雑が続いたかを見るため）。"""
        path = self._p(KYOU)
        with self._lock:
            d = _yomu(path, {}) or {}
            e = d.get(aite) or {}
            if e.get("hi") != self.today or e.get("run") != self.run_id:
                e = {"mae": {"hi": e.get("hi"), "tomatta": e.get("tomatta", "")}} if e.get("hi") else {}
            e.update({"hi": self.today, "run": self.run_id, "repo": self.repo})
            e.update(kw)
            d[aite] = e
            _kaku(path, d)

    def _mae_no_kansoku(self, aite):
        """今日より前の、最後の観測日の控え。"""
        e = (self._kyou_yomu() or {}).get(aite) or {}
        if e.get("hi") == self.today:
            return e.get("mae") or {}
        return {"hi": e.get("hi"), "tomatta": e.get("tomatta", "")} if e.get("hi") else {}

    # ---------------------------------------------------------- 相手台帳
    def aite_of_host(self, host):
        if not isinstance(self.daicho, dict):
            return None, None
        for name, a in (self.daicho.get("aite") or {}).items():
            if host in (a.get("host") or []):
                return name, a
        return None, None

    # ---------------------------------------------------------- 金庫
    def kinko_preflight(self, hozon_saki):
        """private の金庫へ書けるかを、**取りに行く前に**確かめる。空の並びならよい。"""
        riyuu = []
        d = self.env.get("KINKO_DIR") or ""
        if not d or not os.path.isdir(d):
            return ["金庫の置き場が渡されていない（KINKO_DIR）"]
        if self.env.get("KINKO_PRIVATE") != "1":
            riyuu.append("金庫が private だと確かめられていない（KINKO_PRIVATE）")
        if not hozon_saki:
            return riyuu + ["保存先が渡されていない"]
        kinko = os.path.realpath(d)
        saki = os.path.realpath(hozon_saki)
        if saki != kinko and not saki.startswith(kinko + os.sep):
            return riyuu + ["保存先が金庫の中に無い（%s）" % hozon_saki]
        try:
            os.makedirs(saki, exist_ok=True)
            fd, t = tempfile.mkstemp(dir=saki, prefix=".kado-")
            os.close(fd)
            os.remove(t)
        except OSError as e:
            riyuu.append("金庫に書けない（%s）" % type(e).__name__)
        return riyuu

    # ---------------------------------------------------------- カードの門
    def card_mon(self, cid, koui=KOUI_TORU, hozon_saki=None):
        """取りに行ってよいか（URL ごとの関所より前の分）。**止める理由の並び**を返す。空なら通す。"""
        card = self.card(cid)
        if self.cards is None:
            return ["カードの置き場が壊れている"]
        if card is None:
            return ["カードが無い（未確認）"]
        riyuu = []
        jotai = self.seishiki(cid)
        if jotai != "取ってよい":
            why = ""
            if card.get("統括判定案") == "取ってよい":
                ok, why = self.shounin(cid)
                if ok:
                    why = "、".join(_kaketeiru(card)) or (
                        "専門家確認が未了" if card.get("専門家確認") == SENMONKA_TOMERU else "")
            riyuu.append("正式状態が「%s」%s" % (jotai, ("（%s）" % why) if why else ""))
        kigen = _hi(card.get("再確認期限"))
        if kigen is not None and _hi(self.today) and _hi(self.today) > kigen:
            riyuu.append("再確認期限（%s）を過ぎた" % kigen)
            if jotai == "取ってよい":
                self.fuda_wo_tsukeru(cid, "再読要", "再確認期限 %s を過ぎた" % kigen)
        f, fr = self.fuda(cid)
        if f != "なし":
            riyuu.append("機械札「%s」%s" % (f, ("（%s）" % fr) if fr else ""))
        seiki = card.get("正規提供手段")
        if seiki not in SEIKI or seiki in ("未調査", "問い合わせ中"):
            riyuu.append("正規提供手段が「%s」" % (seiki or "空"))
        if card.get("source種別") == "二次・集約" and seiki != "有り・採用":
            riyuu.append("二次・集約なのに、正規提供手段が採用されていない")
        ari = card.get("承認対象の行為") or []
        for k in ([koui] if isinstance(koui, str) else koui):
            if k not in ari:
                riyuu.append("承認されていない行為（%s）" % k)
        aite = card.get("相手") or ""
        a = ((self.daicho or {}).get("aite") or {}).get(aite) if isinstance(self.daicho, dict) else None
        if a is None:
            riyuu.append("相手台帳に相手（%s）が無い" % (aite or "空"))
        else:
            if a.get("担当") != self.repo:
                riyuu.append("この相手の担当は「%s」（ここは %s）" % (a.get("担当") or "未定", self.repo))
            hosts = card.get("対象host") or []
            soto = [h for h in hosts if h not in (a.get("host") or [])]
            if soto or not hosts:
                riyuu.append("カードの対象hostが、相手台帳のその相手に無い")
                if jotai == "取ってよい":
                    self.fuda_wo_tsukeru(cid, "不整合", "対象hostが相手台帳と合わない")
            e = (self._kyou_yomu() or {}).get(aite) or {}
            if e.get("hi") == self.today and e.get("run") != self.run_id:
                riyuu.append("この相手は今日もう見た（%s）" % e.get("repo", ""))
            if e.get("hi") == self.today and e.get("tomatta"):
                riyuu.append("この相手は今日「%s」と返した" % e.get("tomatta"))
        if aite in self._tomatta:
            riyuu.append("この回、この相手は「%s」で止めた" % self._tomatta[aite])
        if not riyuu:
            riyuu += self.kinko_preflight(hozon_saki)
        return riyuu

    @contextlib.contextmanager
    def sesshon(self, cid, koui=KOUI_TORU, hozon_saki=None):
        """このカードで取りに行く間だけ、通信を許す。**門で止まったら Tomeru。**"""
        riyuu = self.card_mon(cid, koui, hozon_saki)
        if riyuu:
            self.kiroku.append((cid, "", "止めた", "／".join(riyuu)))
            raise Tomeru(riyuu)
        with self._lock:
            mae = self._genzai
            self._genzai = (cid, self.card(cid), koui)
        try:
            yield self
        finally:
            with self._lock:
                self._genzai = mae

    # ---------------------------------------------------------- URL ごとの関所
    def _matsu(self, aite, delay=None):
        """同じ相手へは、前の1本から5秒（Crawl-delay が長ければそちら）空ける。

        **同じ回の別の処理（別の .py）が出した分も数える。** 最後に出した時刻は今日の控えに残す。
        """
        w = max(MATSU, delay or 0)
        e = (self._kyou_yomu() or {}).get(aite) or {}
        t = self._saigo.get(aite)
        if e.get("hi") == self.today and isinstance(e.get("saigo"), (int, float)):
            t = max(t or 0, e["saigo"])
        if t is not None:
            nokori = t + w - self._now()
            if nokori > 0:
                self._sleep(nokori)
        self._saigo[aite] = self._now()
        self._kyou_kaku(aite, saigo=self._saigo[aite])

    def _robots_toru(self, scheme, host, aite):
        key = (scheme, host)
        if key in self._robots:
            return self._robots[key]
        url = "%s://%s/robots.txt" % (scheme, host)
        self._matsu(aite)              # robots.txt を見に行くのも、その相手への1観測に数える
        handlers = [_TenSouShinai()]
        if self.transport is not None:
            handlers.insert(0, self.transport)
        op = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        status, ctype, body = None, "", b""
        try:
            with op.open(req, timeout=TIMEOUT) as r:
                status, ctype = r.status, r.headers.get("Content-Type", "")
                body = r.read(ROBOTS_OOKISA + 1)
        except urllib.error.HTTPError as e:
            status, ctype = e.code, e.headers.get("Content-Type", "") if e.headers else ""
        except Exception as e:                                   # noqa: BLE001
            v = ("確かめられなかった", None, None,
                 "robots.txt に届かなかった（%s）" % type(e).__name__)
            self._robots[key] = v
            return v
        jotai, groups, why = robots_hantei(status, ctype, body)
        rules, delay = robots_group(groups, self.tokens) if groups else ([], None)
        v = (jotai, rules if jotai == "通す" else None, delay, why)
        self._robots[key] = v
        if jotai == "混んでいる":
            self._tomeru(aite, "robots.txt が HTTP %s" % status)
        return v

    def _tomeru(self, aite, riyuu):
        self._tomatta[aite] = riyuu
        mae = self._mae_no_kansoku(aite)
        self._kyou_kaku(aite, tomatta=riyuu)
        if "429" in riyuu or "503" in riyuu or "Retry-After" in riyuu:
            if mae.get("tomatta") and ("429" in mae["tomatta"] or "503" in mae["tomatta"]):
                cur = self._genzai
                if cur:
                    self.fuda_wo_tsukeru(cur[0], "混雑継続", "前の観測日（%s）も混んでいた" % mae.get("hi"))

    def url_mon(self, url):
        """その1本を出してよいか。止めるなら Tomeru。"""
        with self._lock:
            cur = self._genzai
            if cur is None:
                raise Tomeru("カードの門を通っていない通信（セッションの外）")
            cid, card, _ = cur
            u = urllib.parse.urlsplit(url)
            host = (u.hostname or "").lower()
            if u.scheme not in ("http", "https") or not host:
                raise Tomeru("http(s) でない URL")
            if u.username or u.password:
                raise Tomeru("URL に認証の情報が入っている")
            if re.search(r"(^|/)\.{1,2}(/|$)", _seiki(u.path)):
                # 相手の側で別の場所を指せてしまう（範囲と robots の照合をすり抜ける）
                raise Tomeru("URL の道すじに . や .. が入っている")
            aite, a = self.aite_of_host(host)
            if aite is None or aite != card.get("相手"):
                raise Tomeru("この URL の host（%s）は、カードの相手のものではない" % host)
            if a.get("担当") != self.repo:
                raise Tomeru("この相手の担当ではない")
            if host not in (card.get("対象host") or []):
                raise Tomeru("カードの対象hostに無い（%s）" % host)
            han = (card.get("承認する取得方法") or {}).get("URL範囲") or []
            if not any(isinstance(p, str) and p and url.startswith(p) for p in han):
                raise Tomeru("承認された URL範囲の外")
            if aite in self._tomatta:
                raise Tomeru("この回、この相手は「%s」で止めた" % self._tomatta[aite])
            e = (self._kyou_yomu() or {}).get(aite) or {}
            if e.get("hi") == self.today and (e.get("run") != self.run_id or e.get("tomatta")):
                raise Tomeru("この相手は今日もう見た、または止めた")
            jotai, rules, delay, why = self._robots_toru(u.scheme, host, aite)
            if jotai != "通す":
                raise Tomeru("robots が「%s」：%s" % (jotai, why))
            if not robots_yurusu(rules, url):
                self.fuda_wo_tsukeru(cid, "拒否継続", "承認された URL を robots が拒否した（%s）" % url)
                raise Tomeru("robots が「拒否」")
            self._matsu(aite, delay)
            self.kiroku.append((cid, url, "出した", ""))
            return aite

    def opener(self):
        k = self

        class _Mihari(urllib.request.BaseHandler):
            handler_order = 100

            def http_request(self, req):
                req.add_unredirected_header("User-Agent", k.ua)
                req._kado_aite = k.url_mon(req.full_url)
                return req

            https_request = http_request
            ftp_request = http_request       # ftp は http(s) でないので、門で止まる

            def http_response(self, req, resp):
                code = getattr(resp, "status", None) or resp.getcode()
                aite = getattr(req, "_kado_aite", None)
                if aite:
                    if code in KONDA or resp.headers.get("Retry-After"):
                        k._tomeru(aite, "HTTP %s%s" % (code, "（Retry-After）" if resp.headers.get("Retry-After") else ""))
                    elif code in KOTOWARI:
                        k._tomeru(aite, "HTTP %s（断られた）" % code)
                return resp

            https_response = http_response

        handlers = [_Mihari()]
        if self.transport is not None:
            handlers.insert(0, self.transport)
        return urllib.request.build_opener(*handlers)

    def install(self):
        urllib.request.install_opener(self.opener())
        global _KADO
        _KADO = self
        return self

    def robots_kekka(self, url):
        """既存の robots 関数の置き換え。(True/False/None, 理由)。**セッションの中でだけ**。"""
        cur = self._genzai
        if cur is None:
            return None, "カードの門を通っていない"
        u = urllib.parse.urlsplit(url)
        aite, _ = self.aite_of_host((u.hostname or "").lower())
        if aite is None or aite != cur[1].get("相手"):
            return None, "相手台帳に無い host"
        if aite in self._tomatta:
            return None, "この回、この相手は止めた"
        jotai, rules, _, why = self._robots_toru(u.scheme, (u.hostname or "").lower(), aite)
        if jotai == "拒否":
            return False, why
        if jotai != "通す":
            return None, why
        return (True, why) if robots_yurusu(rules, url) else (False, "robots.txt で拒否されている")


_KADO = None


def genzai():
    """install した門。無ければ None（そのときは通信も出ない）。"""
    return _KADO


def hajimeru(root, repo, ua, **kw):
    """実行の最初に呼ぶ。**呼ばずに通信しようとしても、urllib の既定の opener が無いので止まる。**"""
    return Kado(root, repo, ua, **kw).install()


class _Tozasu(urllib.request.BaseHandler):
    handler_order = 100

    def http_request(self, req):
        raise Tomeru("門（common/kado.py）が始まっていない。hajimeru() を呼んでいない通信")

    https_request = http_request
    ftp_request = http_request


# **import した時点で、門の無い通信を閉じる。** hajimeru() が門を入れるまで、何も出ない
urllib.request.install_opener(urllib.request.build_opener(_Tozasu()))


# ------------------------------------------------------------------ 一覧
ICHIRAN = os.path.join("data", "ref", "card-ichiran.md")


def koho_ka(card):
    """step 0 の「取ってよい候補」か。既存の「取ってよい」に、読んだ日・根拠・在りかが残っているもの。"""
    m = card.get("移行元") or {}
    return m.get("旧値") == "取ってよい" and all(m.get(k) for k in ("確認日", "根拠", "在りか"))


def ichiran_md(k):
    """カードの一覧。**統括判定案と運営者承認が要るもの**を、この順に出す。機械が書く。"""
    if not isinstance(k.cards, dict):
        return "# 取得元カードの一覧\n\n**カードの置き場が読めない。どれも取りに行かない。**\n"
    gyou = []
    for cid, c in sorted(k.cards.items()):
        jotai = k.seishiki(cid)
        riyuu = k.card_mon(cid) if jotai == "取ってよい" else []
        m = c.get("移行元") or {}
        a = ((k.daicho or {}).get("aite") or {}).get(c.get("相手") or "") or {}
        kubun = ("取ってよい候補" if koho_ka(c) else
                 m.get("旧値") if m.get("旧値") in ("取ってはいけない", "規約未確定") else "未確認")
        gyou.append((kubun, cid, jotai, m.get("旧値") or "—", "制限の文言あり" if m.get("制限文言") else "",
                     c.get("相手") or "（空）", a.get("担当") or "（台帳に無い）",
                     c.get("カード版"), card_shimon(c), "／".join(riyuu)))
    jun = {"取ってよい候補": 0, "規約未確定": 1, "取ってはいけない": 2, "未確認": 3}
    gyou.sort(key=lambda x: (jun.get(x[0], 9), x[1]))
    out = ["# 取得元カードの一覧", "",
           "**機械（`common/kado.py`）が書く。手で直さない。** 正式状態はカードと運営者承認から導いた値。",
           "承認のしかたは `data/ref/shounin/README.md`。",
           "", "| step 0 の区分 | 件数 |", "| --- | ---: |"]
    for kb in ("取ってよい候補", "未確認", "規約未確定", "取ってはいけない"):
        out.append("| %s | %d |" % (kb, sum(1 for g in gyou if g[0] == kb)))
    out += ["", "## 統括判定案・運営者承認が要るもの（全部）", "",
            "| カード | step 0 の区分 | 正式状態 | 移行元の語 | 控え | 相手 | 担当 | 版 | カード指紋 |",
            "| --- | --- | --- | --- | --- | --- | --- | ---: | --- |"]
    for g in gyou:
        out.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | `%s` |" % (
            g[1], g[0], g[2], g[3], g[4], g[5], g[6], g[7], g[8]))
    daicho = (k.daicho or {}).get("aite") or {}
    jibun = {c.get("相手") for c in k.cards.values() if isinstance(c, dict) and c.get("相手")}
    mitei = sorted(n for n in jibun if (daicho.get(n) or {}).get("担当") != k.repo)
    out += ["", "## この置き場のカードに出てくる相手のうち、ここが直接取りに行けないもの", "",
            "担当がほかの置き場か「未定」、または相手台帳に無い相手。統括・運営者が担当を決めるまで、ここからは行かない。", ""]
    out += ["- %s（担当：%s）" % (n, (daicho.get(n) or {}).get("担当") or "台帳に無い") for n in mitei] or ["- なし"]
    return "\n".join(out) + "\n"


def ichiran_kaku(root, repo, ua="kujiraya archive bot"):
    k = Kado(root, repo, ua)
    path = os.path.join(root, ICHIRAN)
    with open(path, "w", encoding="utf-8") as f:
        f.write(ichiran_md(k))
    return path


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "ichiran":
        print(ichiran_kaku(sys.argv[2], sys.argv[3]))
    else:
        print("使い方: python3 common/kado.py ichiran <置き場の根> <置き場の名前>")
