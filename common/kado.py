"""取りに行く前の門。**この門を通らない通信は、1本も出さない。**

正本 3.4・3.4a（公開情報の取得と事実データの利用。2026-09-29）と、
各サイトの固定線を、取得の入口1か所で守る。旧「自動取得・スクレイピング運用基準 v1
（rev2・rev3）」「民間公開Web v3」などは履歴で、3.4a と食い違うところは判定に使わない。

    カード      取得元ごとの判定カード（data/ref/torimoto-card.json）
    承認        運営者の承認（data/ref/shounin/<カード>.json）。1カード1ファイル
    機械札      機械が付ける停止の札（data/ref/kikai-fuda.json）。4語とは別
    相手台帳    相手・host・担当の置き場（data/ref/aite-daicho.json）
    今日の控え  相手ごとの「今日もう見たか」（data/ref/aite-kyou.json）

## 正式状態は入力しない。導出する

カードの「統括判定案」と、運営者の承認と、承認したカード版・カード指紋から決める。

## 承認した者と、承認ファイルに書き込んだ者は別（2026-09-28）

**承認できるのは運営者だけ**（承認ファイルの `approved_by` が `operator`）。AI・Codex などは
判断者にも承認者にもならない。運営者がはっきり承認した中身を、運営者・Codex・Claude などが
承認ファイルへ**転記**してよい。転記した者は `entered_by` に書く。

**commit した者から、承認した者を推し量らない。** AI の印のある commit でも AI の承認ではなく、
印の無い commit でも運営者が承認したことにはならない（前は印を見ていたが、印は Claude のものしか
見分けられず、Codex の転記を人の入力と数え、改名で印の判定もすり抜けた）。

    門が確かめること    承認ファイルの中身（approved_by が operator・entered_by がある・
                        カード版・カード指紋・日付）と、そのファイルが保存（commit）されていて、
                        保存のあとで書き換えられていないこと
    運用で担保すること  運営者が本当にその中身を承認したこと（承認を受けてから転記する。
                        どこで承認が出たかは approval_source に書ける）。**門はここを確かめられない**

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
        "混雑継続", "拒否継続", "引用不一致", "指紋ゆらぎ", "退役")
KOJIN = ("はい", "いいえ", "分からない")
SHUBETSU = ("行政", "準公的", "民間一次", "二次・集約")
SEIKI = ("未調査", "無し", "有り・採用", "有り・不採用", "問い合わせ中")
SENMONKA_TOMERU = "取得開始前に必要"
KONKYO = ("1", "2", "3", "4", "5")
# 4 は行政等の公式な再利用条件。民間・二次集約には使えない（正本 3.4a）
KONKYO_GYOSEI_DAKE = ("4",)
# 5 は「公開された事実・数値の、通常の公開経路からの観測（STOP 条件に当たらないことを確認した
# 記録つき）」（正本 3.4a）。**明示の許可が書いていないことは、それだけでは止める理由にしない**。
# そのかわり、STOP 条件の全部について、確かめた記録が要る
KONKYO_KANSOKU = "5"
# 正本 3.4a の自動取得の STOP 条件（①〜⑩）。カードの「STOP条件の確認」の鍵
STOP_JOKEN = ("robots", "アクセス制御", "明示的なbot禁止", "相手からの回答", "拒否状態", "負荷",
              "同意済みの契約", "個人情報", "二次・集約", "想定外")
# 「当たる」と書いた STOP 条件は、どの根拠番号でも「取ってよい」にしない
STOP_ATARU = re.compile(r"^\s*当たる")
# route の種別（正本 3.4a。同じ会社でも別 route。1 route＝1カード）
ROUTE = ("web", "api", "member", "manual")
ROUTE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}\Z")
# 商品用の継続観測にしてよいか（正本 3.4a）。「可」でなければ本番の継続観測をしない
#   外す      事実・数値・データそのものの DB 化・蓄積・収集・商用利用の禁止が明示されている
#   読めない  規約が事実データを対象にしているか、DB 化・商用利用に当たるかが読めない
#             （取得可否確認・構造確認・規約確認までにとどめる）
SHOUHIN_KAHI = ("可", "外す", "読めない")
# そのrouteで取ったものを商品に使えるか（API・会員の契約が第三者提供などを禁じていれば「使えない」）
ROUTE_SHOUHIN = ("使える", "使えない", "未確認")
# 取得を止める不確定事項が無いこと（商品化の側の未確認は「商品化の未確認事項」の欄で持つ）
FUKAKUTEI_NASHI = ("なし", "取得を止める不確定事項なし")
# 観測の記録に持たせる出どころ（正本 3.4a）。manual はさらに記録者の役割・見た URL
DEMOTO = ("source_id", "route_id", "route_kind", "observed_at")
DEMOTO_MANUAL = ("recorder_role", "source_url")
# manual の route（人が普通のブラウザで見て、事実だけを書く）。初めのきつい側の既定値は、同じ相手に1日1回まで（route をまたぐ）
MANUAL_HINDO = ("1日1回", "週1回", "月2回", "月1回")
# manual の履歴（追記。相手・route・日。頻度を数えるのに使う。読めなければ止める）
MANUAL_RIREKI = os.path.join("data", "ref", "manual-rireki.json")
MANUAL_RIREKI_SCHEMA = 1
# カードの対象host の1つ1つ（小文字の hostname。scheme・道すじ・空白・末尾の . は入れない）
HOSTNAME = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
KANSOKU_SCHEMA = 1
# 個人情報・個票の取得前ゲート（正本 3.1・3.4a）。カードの欄の名前
KOJIN_GATE_SANCHI = ("当事者に個人がありうる", "氏名を含みうる", "個人の電話番号を含みうる",
                     "個人の生活住所を含みうる")
KOJIN_GATE_KISAI = ("個票の粒度", "所在地の扱い", "privateに保存する予定", "publicに出す予定",
                    "公開時の粒度")
MIKETSU = ("未確認", "未決", "分からない")
ROBOTS = ("通す", "拒否", "混んでいる", "確かめられなかった")
# 1回の取得で、承認に含まれていなければならない行為。**取ったものは必ず金庫に残す**ので、
# 取るだけで内部保存もしている
KOUI_TORU = ("自動取得", "内部保存")
MATSU = 5                      # 同じ相手に続けて出すときに空ける秒数（固定線）
TIMEOUT = 40
ROBOTS_OOKISA = 512000         # これより大きい robots.txt は読みきれないので「確かめられなかった」
KONDA = (429, 503)
KOTOWARI = (401, 403)

# 予約台帳（repo横断・同じ相手・同じ JST 日）。正本の相手台帳の "yoyaku" が要るかを決める
YOYAKU_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")      # 相手ID は ASCII の固定ID
YOYAKU_NINSHIKI = ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")
YOYAKU_SCHEMA = 1
YOYAKU_MICHI = "yoyaku"          # 本番の予約の置き場。ダミー競合試験は "test"
YOYAKU_KAISU = 5                 # push が通らないときに積み直す上限
JST = datetime.timezone(datetime.timedelta(hours=9), "JST")

CARD = os.path.join("data", "ref", "torimoto-card.json")
SHOUNIN = os.path.join("data", "ref", "shounin")
FUDA_PATH = os.path.join("data", "ref", "kikai-fuda.json")
DAICHO = os.path.join("data", "ref", "aite-daicho.json")
KYOU = os.path.join("data", "ref", "aite-kyou.json")

# 承認できる者（approved_by）。**運営者だけ。** AI・Codex・自動実行は承認しない（転記はしてよい）
SHOUNIN_DEKIRU = ("operator",)
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
    """この実行の日付（日本時間）。**門は時計を見ない。**

    置き場ごとに「日付を決める1か所」がある（`RUN_DATE`、または `hajimeru(today=...)` で渡す）。
    門が別に時計を見ると、1回の実行の中で日付が食い違う。
    **読めない RUN_DATE は、黙って今日に倒さず落とす。** 渡されていなければ None
    （そのときは、日付が要る関所で止める）。
    """
    env = os.environ if env is None else env
    d = env.get("RUN_DATE")
    if d is None or d == "":
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or _hi(d) is None:
        raise ValueError("RUN_DATE が読めない：%r（黙って今日に倒さない）" % d)
    return d


def _hi(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------ 承認の出どころ
def _git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


def shounin_hozon(root, relpath):
    """承認ファイルが保存（commit）されていて、保存のあとで書き換えられていないか。(よい, 理由)。

    **誰が commit したかは見ない**（承認した者は、ファイルの approved_by で読む。上の説明）。
    """
    try:
        r = _git(root, "ls-files", "--error-unmatch", relpath)
    except (OSError, subprocess.SubprocessError):
        return False, "git が使えない。承認ファイルが保存されたものか確かめられない"
    if r.returncode != 0:
        return False, "承認ファイルが保存（commit）されていない"
    if _git(root, "diff", "--quiet", "HEAD", "--", relpath).returncode != 0:
        return False, "承認ファイルが、保存したあとで書き換えられている"
    return True, ""


def shounin_sha(s):
    """承認した者と、転記した者。止める理由（空文字ならよい）。"""
    if s.get("approved_by") not in SHOUNIN_DEKIRU:
        return "承認できるのは運営者だけ（approved_by が operator でない：%s）" % (s.get("approved_by") or "空")
    e = s.get("entered_by")
    if not isinstance(e, str) or not e.strip():
        return "承認ファイルに書き込んだ者（entered_by）が書いていない"
    return ""


# ------------------------------------------------------------------ 対象host
def taisho_host(card):
    """カードの対象host を hostname の並びで返す。**形が違えば None**（監査 B-03）。

    並び（list）で、1つ以上あり、1つ1つが小文字の hostname の文字列であること。**文字列1本を並びとして
    扱わない**（`in` が部分文字列の判定になり、"not-example.com" の中に "example.com" があることになる）。
    照合は、この並びの要素との完全一致だけ。
    """
    h = card.get("対象host") if isinstance(card, dict) else None
    if not isinstance(h, list) or not h:
        return None
    if not all(isinstance(x, str) and HOSTNAME.fullmatch(x) for x in h):
        return None
    return list(h)


def _url_host(url):
    """http(s) の URL の hostname（小文字）。読めない・http(s) でない・host が無いなら None。

    **user・password（userinfo）が入った URL も None**（人や機械を、認証・ログイン・アクセス制御の回避に使わない。
    出どころの source_url に認証の情報を残さない。独立再監査 2026-09-30）。
    """
    if not isinstance(url, str) or url != url.strip() or any(c.isspace() for c in url):
        return None
    try:
        u = urllib.parse.urlsplit(url)
        h = u.hostname
        dare = (u.username, u.password)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not h:
        return None
    if dare != (None, None) or "@" in u.netloc:
        return None
    return h


# ------------------------------------------------------------------ 正式状態
def _manual_kaketeiru(card):
    """manual の route の「取ってよい」（人が見て記録してよい）に要る欄のうち、空いているもの（正本 3.4a）。

    **robots の拒否は、manual の拒否ではない**（robots は機械への指示）。そのかわり、人をアクセス制御の
    回避に使わない：人にもログイン・CAPTCHA・チャレンジ・403 などが出るなら、manual にもしない。
    """
    nai = []
    if not ROUTE_ID.match(str(card.get("source_id") or "")):
        nai.append("source_id（英小文字・数字・-）")
    if not str(card.get("対象URL") or "").startswith("https://"):
        nai.append("対象URL（人が見るページ）")
    hosts = taisho_host(card)
    if hosts is None:
        nai.append("対象host（hostname の並び。1本の文字列は不可）")
    elif _url_host(card.get("対象URL")) not in hosts:
        nai.append("対象URL の host が、対象host の並びに無い")
    if card.get("人の通常閲覧") != "できる":
        nai.append("人の通常閲覧（一般公開・ログイン不要で、人が普通のブラウザで見られる＝できる）")
    if card.get("アクセス制御") != "なし":
        nai.append("アクセス制御（人にもログイン・CAPTCHA・チャレンジ・403 などが出るなら manual にしない）")
    kinshi = str(card.get("manual観測を禁じる文言・回答") or "")
    if not kinshi.startswith("なし：") or not kinshi[3:].strip():
        nai.append("manual観測を禁じる文言・回答（「なし：確かめた中身」の形。規約と相手の回答を見る）")
    n = card.get("記録件数上限")
    if not (type(n) is int and n >= 1):
        nai.append("記録件数上限（1回に記録する件数の上限。1以上の整数）")
    if card.get("頻度") not in MANUAL_HINDO:
        nai.append("頻度（%s のどれか。1日1回を超えない）" % "・".join(MANUAL_HINDO))
    for k in KOJIN_GATE_SANCHI:
        if card.get(k) not in KOJIN:
            nai.append("%s（はい／いいえ／分からない）" % k)
    for k in KOJIN_GATE_KISAI:
        v = str(card.get(k) or "").strip()
        if not v or v in MIKETSU:
            nai.append("%s（未決）" % k)
    if card.get("商品用継続観測の可否") != "可":
        nai.append("商品用継続観測の可否が「可」でない")
    if not _hi(card.get("再確認期限")):
        nai.append("再確認期限")
    return nai


def _kaketeiru(card):
    """「取ってよい」に要る欄のうち、空いているもの。"""
    if card.get("route種別") == "manual":
        return _manual_kaketeiru(card)
    nai = []
    shoseki = card.get("規約証跡") or {}
    hoho = card.get("承認する取得方法") or {}
    if not _hi(card.get("規約確認日")):
        nai.append("規約確認日")
    if not (shoseki.get("規約URL") or "").startswith("http"):
        nai.append("規約URL")
    bango = str(card.get("肯定根拠番号") or "")
    if bango not in KONKYO:
        nai.append("肯定根拠番号")
    elif bango in KONKYO_GYOSEI_DAKE and card.get("source種別") not in ("行政", "準公的"):
        # 4（行政等の公式な再利用条件）は行政・準公的のためのもの（正本 3.4a）
        nai.append("肯定根拠番号（4 は行政・準公的の取得元だけ）")
    # route と出どころ（正本 3.4a）。1 route＝1カード。観測の記録にそのまま載る
    route = card.get("route種別")
    if route not in ROUTE:
        nai.append("route種別（web／api／member／manual）")
    if not ROUTE_ID.match(str(card.get("source_id") or "")):
        nai.append("source_id（英小文字・数字・-）")
    if taisho_host(card) is None:
        nai.append("対象host（hostname の並び。1本の文字列は不可）")
    # STOP 条件（正本 3.4a ①〜⑩）。「当たる」と書いたものがあれば、どの根拠でも取ってよいにしない
    joken = card.get("STOP条件の確認") if isinstance(card.get("STOP条件の確認"), dict) else {}
    ataru = [k for k, v in joken.items() if isinstance(v, str) and STOP_ATARU.match(v)]
    if ataru:
        nai.append("STOP 条件に当たる（%s）" % "・".join(ataru))
    if bango == KONKYO_KANSOKU:
        # 根拠5：公開された事実・数値を、通常の公開経路（web）から観測する。
        # 当たらなかった STOP 条件を全部、確かめた中身つきで書く（「該当文言なし」だけは不可）
        if route != "web":
            nai.append("肯定根拠番号（5 は通常の公開経路 web の route だけ）")
        for k in STOP_JOKEN:
            v = joken.get(k)
            if not isinstance(v, str) or not v.strip() or GAITOU_NASHI.match(v):
                nai.append("STOP条件の確認：%s（確かめた中身を書く）" % k)
    # 商品用の継続観測にしてよいか（正本 3.4a）。「可」でなければ本番の継続観測をしない
    kahi = card.get("商品用継続観測の可否")
    if kahi == "外す":
        nai.append("商品用の継続観測から外した取得元（事実データそのものの DB 化・商用利用の禁止が明示）")
    elif kahi != "可":
        nai.append("商品用継続観測の可否が読めない（取得可否確認・構造確認までにとどめる）")
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
    # 個人情報・個票の取得前ゲート（正本 3.1・3.4a）。**未決なら取得を始めない。**
    # 「含みうるか」の欄は はい／いいえ／分からない（分からないは、はいとして扱う）。
    # 保存・公開の予定と粒度は、中身を書いてあること（未確認・未決・分からない は未決）
    for k in KOJIN_GATE_SANCHI:
        if card.get(k) not in KOJIN:
            nai.append("%s（はい／いいえ／分からない）" % k)
    for k in KOJIN_GATE_KISAI:
        v = str(card.get(k) or "").strip()
        if not v or v in MIKETSU:
            nai.append("%s（未決）" % k)
    # **取得を止める**不確定事項が無いこと。商品化の側の未確認は「商品化の未確認事項」で持ち、ここでは見ない
    if card.get("不確定事項") not in FUKAKUTEI_NASHI:
        nai.append("取得を止める不確定事項が残っている（不確定事項が「なし」でない）")
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


# ------------------------------------------------------------------ 出どころ（route provenance）
def _jikoku(s):
    """時差つきの ISO 8601 を epoch 秒に。読めない・時差が無いものは None（推し量らない）。
    出どころの observed_at を見る（4つの置き場の共通の部分。取得可否確認の部分には頼らない）。"""
    try:
        t = datetime.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo is not None else None


def demoto(cid, card, observed_at, source_url="", recorder_role=""):
    """観測の記録に付ける出どころ（正本 3.4a）。カードに route が無ければ ValueError。

    route_id はカードの id（1 route＝1カード）。**出どころの分からない1つの値に混ぜない**ために、
    観測の記録は1件ずつこれを持つ。
    """
    if not isinstance(card, dict):
        raise ValueError("カードが無い（%s）" % cid)
    route, sid = card.get("route種別"), str(card.get("source_id") or "")
    if route not in ROUTE or not ROUTE_ID.match(sid) or not ROUTE_ID.match(str(cid or "")):
        raise ValueError("カード %s に route種別・source_id が無い（出どころを書けない）" % cid)
    if _jikoku(observed_at) is None:
        raise ValueError("observed_at が日時でない（時差まで書く）：%s" % observed_at)
    d = {"source_id": sid, "route_id": cid, "route_kind": route, "observed_at": observed_at}
    if source_url:
        d["source_url"] = source_url
    if route == "manual":
        if not (recorder_role or "").strip() or not (source_url or "").startswith("http"):
            raise ValueError("manual の観測は、記録者の役割と見た URL が要る")
        d["recorder_role"] = recorder_role
    return d


def demoto_tarinai(rec):
    """観測の記録の出どころで、**欠けている・形の違う**欄の並び（空ならそろっている）。

    保存の境界（kansoku_hozon・manual_kiroku）で使う。**demoto() で作ったことを前提にしない**（監査 B-01）：
    source_id・route_id は英小文字・数字・-、route_kind は4つのどれか、observed_at は時差つきの ISO 8601、
    source_url は（あれば。manual は必ず）http(s) の URL、manual の recorder_role は空でない文字列。
    """
    d = rec.get("demoto") if isinstance(rec, dict) else None
    if not isinstance(d, dict):
        return ["demoto"]
    nai = [k for k in ("source_id", "route_id") if not (isinstance(d.get(k), str) and ROUTE_ID.match(d[k]))]
    kind = d.get("route_kind")
    if not (isinstance(kind, str) and kind in ROUTE):
        nai.append("route_kind")
    if not (isinstance(d.get("observed_at"), str) and _jikoku(d["observed_at"]) is not None):
        nai.append("observed_at")
    if ("source_url" in d or kind == "manual") and _url_host(d.get("source_url")) is None:
        nai.append("source_url")
    if kind == "manual" and not (isinstance(d.get("recorder_role"), str) and d["recorder_role"].strip()):
        nai.append("recorder_role")
    return nai


def _manual_hindo(rireki, cid, aite, hindo, today):
    """manual の頻度（正本 3.4a。監査 B-02）。止める理由の並び（空ならよい）。

    **同じ相手は、route をまたいで1日1回まで**（日本時間の同じ日。初めのきつい側の既定値）。そのうえで、その route は
    カードの頻度まで。**前の記録からの日数で数える**（rolling window。統括判断 2026-09-30）：
    1日1回＝同じ日に1回、週1回＝前の記録から7日未満なら止める、月1回＝前の記録から30日未満なら止める、
    月2回＝直近30日未満に2件あれば止める。**暦の週・月の境目ではリセットしない**（相手への負荷を抑える線で、
    集計の週・月ではない。月末→月初、日曜→月曜だけで続けて記録できないようにする）。
    **manual-rireki.json に今日より後の日が1件でもあれば、相手・route を問わず止める**（台帳そのものの時刻の
    整合・破損を疑う条件。特定の相手の頻度の問題ではない。統括判断 2026-09-30）。
    """
    t = _hi(today)
    riyuu = []
    if t is None:
        return ["今日の日付が読めない"]
    if any(_hi(r["hi"]) > t for r in rireki):
        riyuu.append("manual の履歴に、今日より後の日の記録がある（相手・route を問わず、台帳の時刻を信じない）")
    mae = [r for r in rireki if r["aite"] == aite]
    if any(_hi(r["hi"]) == t for r in mae):
        riyuu.append("この相手は今日もう manual で記録した（route をまたいで1日1回）")
    # (回数, 日数)：その route の、今日から数えて「日数」未満の記録が「回数」に達していたら止める
    waku = {"1日1回": (1, 1), "週1回": (1, 7), "月1回": (1, 30), "月2回": (2, 30)}.get(hindo)
    if waku is None:
        return riyuu + ["頻度が読めない（%s）" % hindo]
    n, hi_su = waku
    if sum(1 for r in rireki if r["route_id"] == cid and (t - _hi(r["hi"])).days < hi_su) >= n:
        riyuu.append("この route はカードの頻度（%s：%d日未満に%d回まで）をもう使った" % (hindo, hi_su, n))
    return riyuu


def shouhin_ni_tsukaeru(rows, cards):
    """商品に使ってよい観測の記録だけを残す。(残す, [(外した記録, 理由)])。

    **出どころで絞る**（正本 3.4a）。出どころが欠けた記録・カードの無い route・商品用の継続観測が
    「可」でない取得元・契約で商品に使えない route（API・会員など）のものは外す。
    同じ会社でも route ごとに見る。API の禁止を web へ広げず、web が使えることで API の禁止を無視しない。
    """
    nokosu, hazushita = [], []
    for r in rows:
        nai = demoto_tarinai(r)
        if nai:
            hazushita.append((r, "出どころが欠けている・形が違う（%s）" % "・".join(nai)))
            continue
        c = (cards or {}).get(r["demoto"]["route_id"])
        if not isinstance(c, dict):
            hazushita.append((r, "route のカードが無い（%s）" % r["demoto"]["route_id"]))
        elif c.get("route種別") != r["demoto"]["route_kind"]:
            hazushita.append((r, "記録の route 種別がカードと違う"))
        elif c.get("商品用継続観測の可否") != "可":
            hazushita.append((r, "商品用の継続観測が「可」でない取得元"))
        elif c.get("このrouteのデータを商品に使えるか") != "使える":
            hazushita.append((r, "この route で取ったものは商品に使えない（%s）"
                              % (c.get("このrouteのデータを商品に使えるか") or "未確認")))
        else:
            nokosu.append(r)
    return nokosu, hazushita


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


def robots_kisoku_gyou(body):
    """robots.txt のバイトから、規則の行だけを取り出す。**文字コードを選ばない。**

    役所の robots.txt には Shift_JIS の注記が残っている。規則（User-agent・Allow・
    Disallow 等）は ASCII なので、行ごとに `#` から後ろ（注記）をバイトのまま落とし、
    残った規則の行だけを ASCII として読む。**規則の行に ASCII の外の字があれば、
    どの文字コードの道すじか確かめられないので止める**（推し量って読まない）。
    Shift_JIS の2バイト目に `#` と改行は出てこないので、行と注記の切れ目は壊れない。

    返り値は (規則の行を並べた文字列, 理由)。止めるときは (None, 理由)。
    """
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    gyou = []
    for raw in body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        line = raw.split(b"#", 1)[0].strip()
        if not line:
            continue
        if any(c >= 0x80 for c in line):
            return None, "robots.txt の規則の行に ASCII の外の字がある（文字コードを確かめられない）"
        gyou.append(line.decode("ascii"))
    return "\n".join(gyou), ""


def robots_no_basho(url, saki):
    """転送のあとも、**同じ相手の /robots.txt** か。同じ host の http → https だけは同じ場所として扱う。

    host が変わると、どの host の規則なのか曖昧になる。ほかの場所（トップページ等）へ
    飛ばされた先の 404 は「robots.txt が無い」とは言えない（姉妹の競売統計が実物で決めた形）。
    """
    a = urllib.parse.urlsplit(url)
    b = urllib.parse.urlsplit(saki or url)
    return (b.scheme in ("http", "https") and b.netloc.lower() == a.netloc.lower()
            and b.path == "/robots.txt" and not b.query)


def robots_hantei(status, ctype, body, cenc=""):
    """robots.txt の応答から、4状態のどれかを決める。**分からないものは通さない。**

    返り値は (状態, 規則 or None, 理由)。

    **見出し（Content-Type）ではなく中身で見る。** text/html と名乗って robots.txt を
    返すサーバーはある（姉妹の競売統計が実物で見た）。中身が HTML でなく、規則が読めれば読む。
    text/plain・text/html・無し 以外の見出しは読まない。圧縮されたまま返ってきたら読まない。
    """
    if status in (404, 410):
        return "通す", [], "robots.txt が HTTP %s（置いていない）" % status
    if status in KONDA:
        return "混んでいる", None, "robots.txt が HTTP %s（混んでいる）" % status
    if status is None or not (200 <= status < 300):
        return "確かめられなかった", None, "robots.txt が HTTP %s" % status
    ct = (ctype or "").split(";")[0].strip().lower()
    if ct and ct not in ("text/plain", "text/html"):
        return "確かめられなかった", None, "robots.txt が text/plain で返らなかった（%s）" % ct
    ce = (cenc or "").strip().lower()
    if ce not in ("", "identity"):
        return "確かめられなかった", None, "robots.txt が圧縮されたまま返った（%s）" % ce
    body = body or b""
    if len(body) > ROBOTS_OOKISA:
        return "確かめられなかった", None, "robots.txt が大きすぎて読みきれない"
    atama = body.lstrip(b"\xef\xbb\xbf").lstrip()[:2048].lower()
    if atama.startswith(b"<") or b"<html" in atama or b"<!doctype" in atama:
        return "確かめられなかった", None, "robots.txt のはずが HTML"
    text, why = robots_kisoku_gyou(body)
    if text is None:
        return "確かめられなかった", None, why
    groups, yometa = robots_kaiseki(text)
    if yometa == 0:
        # 空のファイルもここに来る。**200 が返っただけでは許可にしない**
        return "確かめられなかった", None, "robots.txt として読める行が1つも無い（空を含む）"
    return "通す", groups, "robots.txt を読んだ"


# ------------------------------------------------------------------ 門
class _RobotsTenSou(urllib.request.HTTPRedirectHandler):
    """robots.txt の転送は、**同じ相手の /robots.txt へのもの**（http → https 等）だけ辿る。

    転送の先へ出すのも1本の外部通信なので、出す前に日付の関所を通す（門が渡されていれば）。
    """
    max_redirections = 5

    def __init__(self, kado=None):
        super().__init__()
        self.kado = kado

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not robots_no_basho(req.full_url, newurl):
            return None                # よその場所へは辿らない（確かめられなかった）
        if self.kado is not None:
            self.kado.hi_no_seki()     # 日付をまたいでいたら、転送の先へは出さない
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Kado:
    """1回の実行に1つ。"""

    def __init__(self, root, repo, ua, *, ua_tokens=None, today=None, run_id=None,
                 env=None, transport=None, sleep=time.sleep, now=time.time,
                 robots_hikae=None, yoyaku_michi=YOYAKU_MICHI, kansoku_demoto=False):
        self.root = root
        self.repo = repo
        self.ua = ua
        self.tokens = set(ua_tokens or [ua.split("(")[0].strip().lower(),
                                        ua.split()[0].lower()])
        self.env = os.environ if env is None else env
        self.today = today or jst_kyou(self.env)
        if self.today is not None and _hi(self.today) is None:
            raise ValueError("today が読めない：%r" % (self.today,))
        self.run_id = run_id or self.env.get("GITHUB_RUN_ID") or "local-%d" % os.getpid()
        self.transport = transport
        # robots.txt の控えの置き場（金庫の中だけ）。**その日に何と書いてあったか**は
        # 取り直せない。相手が書き換えたら、こちらの控えが唯一の記録になる
        self.robots_hikae = robots_hikae
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
        y = self.daicho.get("yoyaku") if isinstance(self.daicho, dict) else None
        self.yoyaku = y if isinstance(y, dict) else None
        self.yoyaku_michi = yoyaku_michi
        # 観測を出どころつき（Kado.kansoku_hozon）で保存する段か。**そうでない段は本番の取得に入れない**（正本 3.4a）
        self.kansoku_demoto = kansoku_demoto is True
        self._yoyaku_ari = set()       # この処理で予約を証明できた相手
        self._yoyaku_dame = {}         # 相手 -> 予約できなかった理由（この処理ではもう試さない）

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
        why = shounin_sha(s)
        if why:
            return False, why
        return shounin_hozon(self.root, rel.replace(os.sep, "/"))

    def demoto(self, cid, observed_at, source_url="", recorder_role=""):
        """このカード（route）で取った観測の記録に付ける出どころ（正本 3.4a）。"""
        return demoto(cid, self.card(cid), observed_at, source_url, recorder_role)

    def _kansoku_tashikame(self, cid, rows, hozon_saki):
        """観測を金庫へ書く前の確かめ。止める理由の並び（空ならよい）。**書かない。**"""
        card = self.card(cid)
        riyuu = list(self.kinko_preflight(hozon_saki))
        if not isinstance(card, dict):
            riyuu.append("カードが無い（%s）" % cid)
        if not isinstance(rows, list) or not rows:
            riyuu.append("保存する観測が無い")
        else:
            for i, r in enumerate(rows):
                nai = demoto_tarinai(r)
                if nai:
                    riyuu.append("%d件目の出どころが欠けている・形が違う（%s）" % (i + 1, "・".join(nai)))
                    continue
                d = r["demoto"]
                if isinstance(card, dict) and (d["route_id"] != cid or d["route_kind"] != card.get("route種別")
                                               or d["source_id"] != card.get("source_id")):
                    riyuu.append("%d件目の出どころが、このカード（route）と合わない" % (i + 1))
        return riyuu

    def _kansoku_kaku(self, cid, rows, hozon_saki):
        """観測を金庫へ書く。**出どころが1件でも欠けていれば、1件も書かない**（Tomeru）。書いた場所を返す。

        **書き終えてから名前を付ける**：一時ファイルに全部書いてから、名前のある場所へ hard link する
        （上書きしない）。途中で止まっても、書きかけの観測が名前のある場所に残らない。
        """
        riyuu = self._kansoku_tashikame(cid, rows, hozon_saki)
        if riyuu:
            self.kiroku.append((cid, "", "保存しなかった", "／".join(riyuu)))
            raise Tomeru(riyuu)
        body = json.dumps({"schema": KANSOKU_SCHEMA, "route_id": cid, "rows": rows},
                          ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        atama = "%s_%s" % (self.today, re.sub(r"[^0-9A-Za-z-]", "_", str(self.run_id)))
        fd, tmp = tempfile.mkstemp(dir=hozon_saki, prefix=".kansoku-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
                fp.write(body)
                fp.flush()
                os.fsync(fp.fileno())
            for i in range(1, 100):
                path = os.path.join(hozon_saki, "%s%s.json" % (atama, "" if i == 1 else "-%d" % i))
                try:
                    os.link(tmp, path)
                    return path
                except FileExistsError:
                    continue
            raise OSError("観測の名前が100通り埋まっている")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def kansoku_hozon(self, cid, rows, hozon_saki):
        """**本番の観測を保存する境界**（正本 3.4a）。出どころ（source_id・route_id・route_kind・observed_at、
        manual は記録者の役割と見た URL）が欠けた観測は保存しない。書いた場所を返す。"""
        riyuu = []
        if not self.kansoku_demoto:
            riyuu.append("この段は観測を出どころつきで保存する形になっていない（kansoku_demoto）")
        card = self.card(cid)
        if isinstance(card, dict) and card.get("route種別") == "manual":
            riyuu.append("manual の観測は Kado.manual_kiroku で保存する")
        elif self.seishiki(cid) != "取ってよい":
            riyuu.append("正式状態が「%s」" % self.seishiki(cid))
        if riyuu:
            self.kiroku.append((cid, "", "保存しなかった", "／".join(riyuu)))
            raise Tomeru(riyuu)
        return self._kansoku_kaku(cid, rows, hozon_saki)

    def manual_mon(self, cid):
        """manual の route で、人が見て記録してよいか。**止める理由の並び**（空ならよい）。

        機械の取得の門（card_mon・sesshon）とは別の入口。**robots は見ない**（robots の拒否は機械の route を
        止めるもの）。カードの manual の欄・運営者承認（カード版・指紋）・再確認期限・機械札・相手台帳を見る。
        """
        card = self.card(cid)
        if card is None:
            return ["カードが無い（未確認）"]
        if card.get("route種別") != "manual":
            return ["manual の route のカードではない"]
        if self.today is None:
            return ["今日の日付が渡されていない"]
        riyuu = []
        jotai = self.seishiki(cid)
        if jotai != "取ってよい":
            why = "、".join(_kaketeiru(card)) if card.get("統括判定案") == "取ってよい" and self.shounin(cid)[0] else ""
            riyuu.append("正式状態が「%s」%s" % (jotai, ("（%s）" % why) if why else ""))
        kigen = _hi(card.get("再確認期限"))
        if kigen is not None and _hi(self.today) > kigen:
            riyuu.append("再確認期限（%s）を過ぎた" % kigen)
        f, fr = self.fuda(cid)
        if f != "なし":
            riyuu.append("機械札「%s」%s" % (f, ("（%s）" % fr) if fr else ""))
        a = ((self.daicho or {}).get("aite") or {}).get(card.get("相手") or "") if isinstance(self.daicho, dict) else None
        if a is None:
            riyuu.append("相手台帳に相手（%s）が無い" % (card.get("相手") or "空"))
        elif a.get("担当") != self.repo:
            riyuu.append("この相手の担当は「%s」（ここは %s）" % (a.get("担当") or "未定", self.repo))
        return riyuu

    def manual_kiroku(self, cid, rows, observed_at, source_url, recorder_role, hozon_saki):
        """manual の観測を記録する（人が普通のブラウザで見た事実だけ）。書いた場所を返す。**止めるなら Tomeru。**

        1回の件数は、カードの記録件数上限まで。同じ相手は route をまたいで1日1回まで、その route はカードの頻度まで
        （履歴は data/ref/manual-rireki.json）。見た URL の host は、カードの対象host の並びのどれかと完全に一致するときだけ。

        **保存の順は、先に履歴、あとで観測。** 観測を書いたあと・履歴を書く前に止まると、観測だけ残って履歴が無く、
        次の記録を誤って通す。先に履歴を書けば、そのあとで止まっても「履歴だけ残る」（多めに数える＝止まる側）。
        書く前の確かめ（金庫・出どころ）は、履歴を書く前に全部すませる。
        """
        riyuu = self.manual_mon(cid)
        card = self.card(cid) or {}
        # manual_mon（正式状態）に頼らず、ここでも形を見直す（監査 B-03）
        hosts = taisho_host(card)
        if hosts is None:
            riyuu.append("カードの対象host が hostname の並びでない")
        elif not (isinstance(source_url, str) and source_url.startswith("https://")
                  and _url_host(source_url) in hosts):
            riyuu.append("見た URL がカードの対象host の外（%s）" % source_url)
        n = card.get("記録件数上限")
        if not isinstance(rows, list) or not rows:
            riyuu.append("記録する事実が無い")
        elif not (type(n) is int and n >= 1):
            riyuu.append("カードの記録件数上限が読めない")
        elif len(rows) > n:
            riyuu.append("記録件数上限（%d）を超えた（%d）" % (n, len(rows)))
        aite = card.get("相手")
        rireki, why = self._manual_rireki()
        if why:
            riyuu.append(why)
        elif not (isinstance(aite, str) and aite):
            riyuu.append("カードに相手が無い")
        else:
            riyuu += _manual_hindo(rireki, cid, aite, card.get("頻度"), self.today)
        if not riyuu:
            try:
                d = demoto(cid, card, observed_at, source_url, recorder_role)
            except ValueError as e:
                riyuu.append(str(e))
        if not riyuu:
            rows_d = [dict(r, demoto=d) for r in rows]
            riyuu += self._kansoku_tashikame(cid, rows_d, hozon_saki)
        if riyuu:
            self.kiroku.append((cid, "", "記録しなかった", "／".join(riyuu)))
            raise Tomeru(riyuu)
        rireki.append({"hi": self.today, "aite": aite, "route_id": cid, "run": self.run_id, "repo": self.repo})
        _kaku(self._p(MANUAL_RIREKI), {"schema": MANUAL_RIREKI_SCHEMA, "kiroku": rireki})
        return self._kansoku_kaku(cid, rows_d, hozon_saki)

    def _manual_rireki(self):
        """manual の履歴の並びと、読めないときの理由。**読めない・形が違うなら止める側**（数え方を信じない）。

        1行ずつ、保存した形のとおりかを全部見る（独立再監査 2026-09-30）：hi＝YYYY-MM-DD で実在する日、
        aite・run・repo＝空白だけでない文字列、route_id＝ROUTE_ID の形（末尾の改行・道すじ・大文字は不可）。
        **形の違う行が1件でもあれば、履歴の全部を信じない**（頻度の数えに進まない）。
        """
        d = _yomu(self._p(MANUAL_RIREKI), {"schema": MANUAL_RIREKI_SCHEMA, "kiroku": []})
        if not isinstance(d, dict) or d.get("schema") != MANUAL_RIREKI_SCHEMA or not isinstance(d.get("kiroku"), list):
            return None, "manual の履歴（%s）が読めない" % MANUAL_RIREKI
        for r in d["kiroku"]:
            if not (isinstance(r, dict) and isinstance(r.get("hi"), str)
                    and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r["hi"]) and _hi(r["hi"]) is not None
                    and all(isinstance(r.get(k), str) and r[k].strip() for k in ("aite", "run", "repo"))
                    and isinstance(r.get("route_id"), str) and ROUTE_ID.match(r["route_id"])):
                return None, "manual の履歴に形の違う行がある（%s）" % MANUAL_RIREKI
        return list(d["kiroku"]), ""

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
        if card.get("route種別") == "manual":
            return ["manual の route は機械の取得に使わない（人の観測は Kado.manual_kiroku）"]
        riyuu = []
        if self.today is None:
            return ["今日の日付が渡されていない（RUN_DATE・hajimeru の today）。日付が要る関所を確かめられない"]
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
            hosts = taisho_host(card) or []
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
        if self.yoyaku_hitsuyou() and a is not None:
            # 予約の前提のうち、通信の要らないものは、ここで先に見る（予約そのものは最初の通信の直前）
            why = self._yoyaku_mae(aite)
            if why:
                riyuu.append("予約台帳：" + why)
            elif aite in self._yoyaku_dame:
                riyuu.append("予約台帳：" + self._yoyaku_dame[aite])
        if not riyuu:
            riyuu += self.kinko_preflight(hozon_saki)
        return riyuu

    @contextlib.contextmanager
    def sesshon(self, cid, koui=KOUI_TORU, hozon_saki=None):
        """このカードで取りに行く間だけ、通信を許す。**門で止まったら Tomeru。**"""
        if not self.kansoku_demoto:
            # **出どころの無い観測を、本番へ新しく保存させない**（正本 3.4a）。観測を Kado.kansoku_hozon で
            # 保存する段（hajimeru(..., kansoku_demoto=True)）だけが、本番の取得に入れる
            riyuu = ["この段は観測を出どころつきで保存する形になっていない（kansoku_hozon を使う段だけが本番の取得に入れる）"]
            self.kiroku.append((cid, "", "止めた", riyuu[0]))
            raise Tomeru(riyuu)
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

    # ---------------------------------------------------------- 予約台帳（repo横断）
    def yoyaku_hitsuyou(self):
        """予約台帳が要るか。**書いていない・形が違うときは、要る側に倒す**（そのあと前提で止まる）。

        要らないのは、正本の相手台帳が `"yoyaku": {"hitsuyou": false}` と書いたときだけ。置き場の側では外せない。
        """
        return not (isinstance(self.yoyaku, dict) and self.yoyaku.get("hitsuyou") is False)

    def _yoyaku_ninshiki(self):
        """この実行の識別（4つ全部）。どれかが無ければ None（予約できない）。"""
        vals = [str(self.env.get(k) or "") for k in YOYAKU_NINSHIKI]
        if not all(vals):
            return None
        return dict(zip(("repo", "run_id", "run_attempt", "job"), vals))

    def _yoyaku_mae(self, aite):
        """予約の前提のうち、ローカルで確かめられるもの。止める理由（空文字ならよい）。"""
        if not isinstance(self.yoyaku, dict) or self.yoyaku.get("hitsuyou") is not True                 or not isinstance(self.yoyaku.get("repo"), str):
            return "相手台帳に、予約台帳の決まり（yoyaku の hitsuyou・repo）が無い"
        a = ((self.daicho or {}).get("aite") or {}).get(aite) or {}
        aid = a.get("id") or ""
        if not YOYAKU_ID.match(aid):
            return "相手ID（ASCII の固定ID）が相手台帳に無い"
        if self._yoyaku_ninshiki() is None:
            return "この実行の識別（%s）がそろっていない" % "・".join(YOYAKU_NINSHIKI)
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}):\d{2}(?::\d{2})?\+09:?00",
                         str(self.env.get("RUN_HAJIME") or ""))
        if not m:
            return "RUN_HAJIME（日本時間の開始時刻）が読めない"
        if m.group(1) != self.today:
            return "開始の日付（%s）と今日（%s）が違う" % (m.group(1), self.today)
        if m.group(2) == "23":
            return "日本時間23時台に始まった回は予約しない（日付をまたぐため）"
        d = self.env.get("YOYAKU_DIR") or ""
        if not d or not os.path.isdir(os.path.join(d, ".git")):
            return "予約台帳の作業木（YOYAKU_DIR）が無い"
        repo = str(self.yoyaku.get("repo") or "")
        if not repo or self.env.get("YOYAKU_REPO") != repo:
            return "予約台帳の名前（YOYAKU_REPO）が相手台帳と合わない"
        return ""

    def _yoyaku_yomu(self, d, michi, aid):
        """origin/main の予約を読む。("無い", None) ／ ("在る", 中身) ／ ("読めない", 理由)。"""
        r = _git(d, "ls-tree", "--name-only", "origin/main", "--", michi)
        if r.returncode != 0:
            return "読めない", "予約台帳の中を見られない"
        if not r.stdout.strip():
            return "無い", None
        r = _git(d, "show", "origin/main:%s" % michi)
        try:
            s = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            s = None
        if not isinstance(s, dict) or s.get("schema") != YOYAKU_SCHEMA \
                or s.get("hi") != self.today or s.get("aite") != aid \
                or not all(s.get(k) for k in ("repo", "run_id", "run_attempt", "job")):
            return "読めない", "予約のファイルの形が違う（%s）" % michi
        return "在る", s

    def _yoyaku_toru(self, aite):
        """その相手の今日の予約を、この実行が持っていると証明する。止める理由（空文字ならよい）。

        1. 取り込む（fetch）→ 2. 予約が在れば、この実行のものか見る → 3. 無ければ1つ書いて push
        → 4. 取り込み直して、自分の予約が入っていることを確かめる。
        push が通らなければ、取り込み直して 2. からやり直す（上限あり）。先頭の食い違いは
        「[rejected]」でも「[remote rejected]（ref を取れない）」でも出るので、理由では選り分けない。
        やり直しのたびに台帳を読み直すので、ほかの実行が先に取っていれば、そこで止まる。
        **強制 push・上書き・削除はしない。予約は解放しない。**
        """
        why = self._yoyaku_mae(aite)
        if why:
            return why
        d = self.env["YOYAKU_DIR"]
        repo = self.yoyaku["repo"]
        r = _git(d, "remote", "get-url", "origin")
        url = r.stdout.strip().replace("\\", "/")
        url = url[:-4] if url.endswith(".git") else url
        if r.returncode != 0 or not (url.endswith("/" + repo) or url.endswith(":" + repo)):
            return "予約台帳の作業木の取り込み先が違う"
        aid = self.daicho["aite"][aite]["id"]
        jibun = dict(self._yoyaku_ninshiki(), schema=YOYAKU_SCHEMA, hi=self.today, aite=aid,
                     workflow=str(self.env.get("GITHUB_WORKFLOW_REF")
                                  or self.env.get("GITHUB_WORKFLOW") or ""))
        michi = "%s/%s/%s.json" % (self.yoyaku_michi, self.today, aid)
        onaji = lambda s: all(s.get(k) == jibun[k] for k in ("repo", "run_id", "run_attempt", "job"))
        nakami = json.dumps(jibun, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        saigo = "予約の競合が %d 回続いた" % YOYAKU_KAISU
        for _ in range(YOYAKU_KAISU):
            if _git(d, "fetch", "--quiet", "origin", "main").returncode != 0:
                return "予約台帳を取り込めない"
            aru, s = self._yoyaku_yomu(d, michi, aid)
            if aru == "読めない":
                return s
            if aru == "在る":
                if onaji(s):
                    return ""          # 同じ実行の後続（同じ job の別の .py）。自分の予約
                return "今日はほかの実行が、この相手を予約している（%s）" % s.get("repo")
            if _git(d, "checkout", "--quiet", "-B", "yoyaku-kaku", "origin/main").returncode != 0:
                return "予約台帳の作業木をそろえられない"
            moto = _git(d, "rev-parse", "--verify", "-q", "origin/main").stdout.strip()
            if not moto:
                return "予約台帳の先頭が読めない"
            os.makedirs(os.path.join(d, os.path.dirname(michi)), exist_ok=True)
            with open(os.path.join(d, michi), "w", encoding="utf-8", newline="\n") as f:
                f.write(nakami)
            if _git(d, "add", "--", michi).returncode != 0 or _git(
                    d, "-c", "user.name=kujiraya-yoyaku", "-c", "user.email=yoyaku@kujiraya.invalid",
                    "commit", "--quiet", "-m",
                    "予約 %s %s %s#%s/%s/%s" % (self.today, aid, jibun["repo"], jibun["run_id"],
                                              jibun["run_attempt"], jibun["job"])).returncode != 0:
                return "予約を書けない（commit）"
            mine = _git(d, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
            why = self._yoyaku_tsuika_dake(d, mine, moto, michi, nakami)
            if why:
                return why             # **push しない**
            p = _git(d, "push", "--quiet", "origin", "%s:refs/heads/main" % mine)
            if p.returncode == 0:
                if _git(d, "fetch", "--quiet", "origin", "main").returncode != 0:
                    return "予約のあとで取り込み直せない"
                if _git(d, "merge-base", "--is-ancestor", mine, "origin/main").returncode != 0:
                    return "書いた予約が台帳に入っていない"
                # 台帳に入った自分の commit が、追加1件だけのものか（push の前と同じ照合を、入ったあとにも）
                why = self._yoyaku_tsuika_dake(d, mine, moto, michi, nakami)
                if why:
                    return "台帳に入った予約の commit：" + why
                # 書いたあとで、自分の予約がだれかに変えられていないか
                if _git(d, "diff", "--quiet", mine, "origin/main", "--", michi).returncode != 0:
                    return "書いた予約が、あとから変えられた"
                aru, s = self._yoyaku_yomu(d, michi, aid)
                if aru != "在る" or not onaji(s):
                    return "書いた予約を読み直せない"
                return ""
            err = (p.stderr or "").lower()
            if not any(w in err for w in ("rejected", "fetch first", "cannot lock ref")):
                saigo = "予約を push できない"
            # 取り込み直して、同じ相手・同じ日が先に取られていないか見る
        return saigo

    def _yoyaku_tsuika_dake(self, d, rev, moto, michi, nakami):
        """その commit が「予定の予約ファイル1件の追加だけ」か。止める理由（空文字ならよい）。

        - 親はちょうど1つで、取り込んだ台帳の先頭（moto）であること
        - 親との差分が、ちょうど1件「新しい通常のファイル michi の追加」であること。
          改名・写しの見分けは**わざと切る**（見分けると、前の日の予約とよく似た新しい予約が「写し」と
          出てしまう）。切ると、改名は「削除＋追加」の2件になるので、1件でないとして止まる
        - 入ったバイトが、書くつもりだった中身（nakami）と同じであること
        既存のファイルの変更・削除・改名、予定外のファイルの追加が1つでもあれば止める。
        """
        if not rev or not moto:
            return "予約の commit が読めない"
        r = _git(d, "rev-list", "--parents", "-n", "1", rev)
        ids = r.stdout.split() if r.returncode == 0 else []
        if len(ids) != 2 or ids[0] != rev:
            return "予約の commit の親が1つでない"
        if ids[1] != moto:
            return "予約の commit の親が、取り込んだ台帳の先頭でない"
        r = _git(d, "diff-tree", "-r", "-z", "--raw", "--no-commit-id", "--no-ext-diff",
                 "--no-renames", moto, rev)
        if r.returncode != 0:
            return "予約の commit の差分が読めない"
        kire = r.stdout.split("\0")
        if kire and kire[-1] == "":
            kire.pop()
        if len(kire) != 2 or kire[1] != michi:
            shurui = "・".join(sorted({x.split()[-1] for x in kire[0::2] if x.startswith(":")}))
            return "予約の commit に、予定の1件の追加でないものが入っている（%d 件・%s）" % (
                len(kire) // 2, shurui or "読めない")
        m = re.fullmatch(r":000000 100644 0{7,64} ([0-9a-f]{7,64}) A", kire[0])
        if not m:
            return "予約の commit に、予定の1件の追加でないものが入っている"
        b = _git(d, "cat-file", "blob", m.group(1))
        if b.returncode != 0 or b.stdout != nakami:
            return "予約の commit の中身が、書くつもりのものと違う"
        return ""

    def _yoyaku_shoumei(self, aite):
        """最初の外部通信の直前に呼ぶ。予約を証明できなければ Tomeru。"""
        if not self.yoyaku_hitsuyou():
            return
        if aite in self._yoyaku_ari:
            return
        if aite in self._yoyaku_dame:
            raise Tomeru("予約台帳：" + self._yoyaku_dame[aite])
        try:
            why = self._yoyaku_toru(aite)
        except (OSError, subprocess.SubprocessError) as e:
            why = "予約台帳を扱えない（%s）" % type(e).__name__
        if why:
            self._yoyaku_dame[aite] = why
            self.kiroku.append(("", aite, "予約できない", why))
            raise Tomeru("予約台帳：" + why)
        self._yoyaku_ari.add(aite)
        self._kyou_kaku(aite, yoyaku="%s#%s/%s/%s" % tuple(
            self._yoyaku_ninshiki()[k] for k in ("repo", "run_id", "run_attempt", "job")))
        self.kiroku.append(("", aite, "予約した", ""))

    # ---------------------------------------------------------- URL ごとの関所
    def hi_no_seki(self):
        """外部通信の直前に呼ぶ。**いまの日本時間の日付が、この実行の日付（RUN_DATE）と同じか。**

        日付は実行の最初に1回だけ決める（門は時計で日付を決めない）。ここで時計を見るのは、
        日付をまたいだ回を**止める**ためだけ。22時台に始まって0時を越えた回が、翌日の分を
        取りに行かない（予約済みの相手でも、robots.txt でも、転送の先でも）。食い違ったら Tomeru。
        """
        riyuu = ""
        rd = self.env.get("RUN_DATE")
        if self.today is None:
            riyuu = "この実行の日付（RUN_DATE）が無い"
        elif rd and rd != self.today:
            riyuu = "RUN_DATE（%s）と、この実行の日付（%s）が違う" % (rd, self.today)
        else:
            ima = datetime.datetime.fromtimestamp(self._now(), JST).date().isoformat()
            if ima != self.today:
                riyuu = "日本時間の日付が変わった（この実行 %s・いま %s）。この回は、もう外へ出さない" % (
                    self.today, ima)
        if riyuu:
            if not any(x[2] == "日付で止めた" for x in self.kiroku):
                self.kiroku.append(("", "", "日付で止めた", riyuu))
            raise Tomeru(riyuu)

    def _matsu(self, aite, delay=None):
        """同じ相手へは、前の1本から5秒（Crawl-delay が長ければそちら）空ける。

        **同じ回の別の処理（別の .py）が出した分も数える。** 最後に出した時刻は今日の控えに残す。
        ここはその相手への外部通信の直前なので、**日付の関所と、予約台帳の予約をここで確かめる**
        （robots.txt も本体も、転送の先も通る）。
        """
        self.hi_no_seki()
        self._yoyaku_shoumei(aite)
        w = max(MATSU, delay or 0)
        e = (self._kyou_yomu() or {}).get(aite) or {}
        t = self._saigo.get(aite)
        if e.get("hi") == self.today and isinstance(e.get("saigo"), (int, float)):
            t = max(t or 0, e["saigo"])
        if t is not None:
            nokori = t + w - self._now()
            if nokori > 0:
                self._sleep(nokori)
        # 待ったあと、出す直前にもう一度（待つ間に0時を越えることがある。Crawl-delay は長いこともある）
        self.hi_no_seki()
        self._saigo[aite] = self._now()
        self._kyou_kaku(aite, saigo=self._saigo[aite])

    def _robots_toru(self, scheme, host, aite):
        key = (scheme, host)
        if key in self._robots:
            return self._robots[key]
        url = "%s://%s/robots.txt" % (scheme, host)
        self._matsu(aite)              # robots.txt を見に行くのも、その相手への1観測に数える
        handlers = [_RobotsTenSou(self)]
        if self.transport is not None:
            handlers.insert(0, self.transport)
        op = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        status, ctype, cenc, body, saki = None, "", "", b"", url
        try:
            with op.open(req, timeout=TIMEOUT) as r:
                status, ctype = r.status, r.headers.get("Content-Type", "")
                cenc = r.headers.get("Content-Encoding", "")
                saki = r.geturl() or url
                body = r.read(ROBOTS_OOKISA + 1)
        except urllib.error.HTTPError as e:
            status, ctype = e.code, e.headers.get("Content-Type", "") if e.headers else ""
            saki = getattr(e, "url", None) or getattr(e, "filename", None) or url
        except Exception as e:                                   # noqa: BLE001
            v = ("確かめられなかった", None, None,
                 "robots.txt に届かなかった（%s）" % type(e).__name__)
            self._robots[key] = v
            return v
        if not robots_no_basho(url, saki):
            # 転送でよその場所へ行った先の答えは、この相手の robots.txt の答えではない
            v = ("確かめられなかった", None, None,
                 "robots.txt が、よその場所（%s）へ転送された" % saki)
            self._robots[key] = v
            return v
        if status is not None and 200 <= status < 300:
            self._robots_wo_hikaeru(host, url, body)
        jotai, groups, why = robots_hantei(status, ctype, body, cenc)
        rules, delay = robots_group(groups, self.tokens) if groups else ([], None)
        v = (jotai, rules if jotai == "通す" else None, delay, why)
        self._robots[key] = v
        if jotai == "混んでいる":
            self._tomeru(aite, "robots.txt が HTTP %s" % status)
        return v

    def _robots_wo_hikaeru(self, host, url, body):
        """受け取った robots.txt を、**バイトのまま**金庫の中に控える。金庫の外には書かない。

        頭の2行（取得日と出どころ）は控えなので、別のファイルにせずバイトの手前に足す
        （robots.txt は `#` が注記なので、規則とは混ざらない。姉妹の競売統計と同じ形）。
        """
        if not self.robots_hikae:
            return
        kinko = self.env.get("KINKO_DIR") or ""
        saki = os.path.realpath(self.robots_hikae)
        if not kinko or not (saki == os.path.realpath(kinko)
                             or saki.startswith(os.path.realpath(kinko) + os.sep)):
            self.kiroku.append(("", host, "控えない", "robots.txt の控えの置き場が金庫の中に無い"))
            return
        try:
            os.makedirs(saki, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=saki, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(("# 取得日: %s\n# %s\n" % (self.today, url)).encode("utf-8"))
                f.write(body)
            os.replace(tmp, os.path.join(saki, re.sub(r"[^0-9A-Za-z.-]", "_", host) + ".txt"))
        except OSError as e:
            self.kiroku.append(("", host, "控えない", "robots.txt を控えられなかった（%s）" % type(e).__name__))

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
            if host not in (taisho_host(card) or []):
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
        try:
            jotai, rules, _, why = self._robots_toru(u.scheme, (u.hostname or "").lower(), aite)
        except Tomeru as e:            # 予約台帳で止めた等。**通信は出ていない**
            return None, "／".join(e.riyuu)
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
    # **知らない引数で止める**（common/hikisu.py と同じ向き）。0 で終わると、
    # 打ち間違いを workflow が「通った」と読む。余った語も知らない引数として止める
    if len(sys.argv) == 4 and sys.argv[1] == "ichiran":
        print(ichiran_kaku(sys.argv[2], sys.argv[3]))
    else:
        print("使い方: python3 common/kado.py ichiran <置き場の根> <置き場の名前>", file=sys.stderr)
        raise SystemExit(2)
