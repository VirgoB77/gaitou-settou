"""配布元から、県警CSVと e-Stat の町丁目境界を取ってくる。

取得の作法は共通仕様3.4に従う。
同時接続は1本、間隔は5秒以上、429/503 が返ったらその回は中止して次回に回す。
押し込まない。回り込まない。

**通信の手前に門（common/kado.py）がある。** カードごとに `K.card_mon()` を見て、
通ったものだけ `with K.sesshon(...):` の中で取りに行く。門の中で
`urllib.request.urlopen()` を呼ぶと、そのたびに URL範囲・相手台帳・robots.txt
（fail-closed）・間隔（5秒）・429/503/401/403 の関所を通る。**転送（redirect）も
1段ごとに、転送先で同じ関所をやり直す。** 押し込まない・回り込まないは、門がする。

robots.txt の判定（共通仕様3.4）は `common/kado.py` がする。ここは `K.robots_kekka()`
に寄せるだけで、200 なら読む・404/410 は許可・HTML は分からない、等の判定を
モジュール側で重複して持たない。**門が「確かめられなかった」（None）を返したら、
許可として扱わない。**

**捕まえないもの。**

  ・HTML が何の画面か。CSV・ZIP のはずの所に HTML が返ったら、その1本を
    unexpected_html とし、**その取得先にはその回もう行かない**（429・503 と同じ道）。
    待機列・エラーページ・ログイン画面のどれかは見分けていないし、推測もしない。
    記録には見たことだけを書く。次の回はまた見に行く。これは門の外側、
    fetch_data 自身が持つ判断（門は CSV・ZIP の中身までは知らない）
  ・http:// を中継経由で取るとき、中継の 403 と相手の 403 は区別できない。
    取得先は https なので、中継の断りは「届かなかった」として出る

**毎回、設定したものを全部取りに行く。** 手元に同じ名前のファイルがあっても飛ばさない。
飛ばすと、配布元が同じ名前のまま中身を差し替えたときに気づけない。
取れた中身は前回の生データと比べて、

    同じ     生データには触らない（差分を出さない）
    違う     同じ場所を置き換える。前の版は金庫の Git 履歴に残る
    取れない 前回の生データには触らない。途中まで取れたものも書かない

どの場合も、観測の記録（manifest）を1回ぶん残す。
**「この日に見に行って、同じだった」も記録になる。**

置き場（config.RAW。workflow では private の金庫へのリンク）:

    <配布元のファイル名>.csv      県警CSV
    境界_<市名>/                  境界 ZIP を展開したもの（地図を作る段が読む）
    source_zip/境界_<市名>.zip    取ってきた ZIP そのもの（証拠）
    manifest/<観測日時>.json      この回の観測の記録

境界 ZIP は、取るたびにバイト列が変わることがある
（2026-09-18 と 09-21 で、ZIP の大きさが1バイトずつ違った。中身が同じかは確かめていない）。
そのため ZIP は**展開した中身**で比べる。中身が同じなら前の ZIP を残し、
今回の ZIP のバイト列の SHA-256 は manifest に残す。

1本でも取れなかったら、終了コード 1 で終わる（manifest は書く）。
workflow はそれを見て、取れた分と記録だけを金庫にしまい、公開には進まない。
"""

import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

# common/ には __init__.py が無い。置き場の根を sys.path に足すと、
# 名前空間パッケージとして `from common import kado` が通る
# （tests/test_kado.py と同じ寄せ方）。
sys.path.insert(0, str(config.ROOT))
from common import kado  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

# 本体（CSV・ZIP）の応答を待つ時間。門（common/kado.py）が課す間隔・関所とは別。
TIMEOUT = 120

# 作業中のファイルに付ける印。金庫の .gitignore が同じ印を外す。
PART = ".part"


class Halted(Exception):
    """この取得先には、この回もう行かない。残りは次回に回す。やり直さない。

    status は観測の記録に書く名前。**理由を推測で名付けない。見たことだけを書く。**

      halted           429・503 が返った
      unexpected_html  CSV・ZIP のはずが HTML が返った（何の画面かは決めつけない）
    """

    status = "halted"

    def __init__(self, code, message=None):
        super().__init__(message or f"{code} が返りました。この回は中止します（次回に回す）")
        self.code = code


class UnexpectedHtml(Halted):
    """CSV・ZIP のはずが HTML が返った。

    待機列かもしれないし、エラーページやログイン画面かもしれない。
    **どれかは見分けない。** 続けて取りに行くと、待機列だった場合に押し込みになる。
    だから 429・503 と同じく、その取得先にはこの回もう行かない。
    """

    status = "unexpected_html"

    def __init__(self, expected, code=200):
        super().__init__(code, f"{expected} のはずが HTML が返った。"
                               "この取得先には、この回もう行かない（次回に回す）")


class NotData(Exception):
    """HTTP では成功したが、中身が期待した形ではない（エラーページなど）。"""


class RobotsStop(Exception):
    """門（common/kado.py の robots_kekka）が、本体の手前で止めた。回り込まない。

    status は観測の記録に書く名前。**相手の答えと、確かめられなかったことを分ける。**

      robots_disallowed   robots.txt の中身が、この URL を止めている
      robots_unusable     止めていないと確かめられない（届かない・HTML・混んでいる 等）。
                          **確かめられなかったものを、許可として扱わない（fail-closed）**
    """

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RobotsDenied(RobotsStop):
    """相手の robots.txt が、この URL を止めている。"""


class RobotsUnknown(RobotsStop):
    """止めていないと確かめられない（門の判定をそのまま受け取る）。"""


def robots_for(url):
    """url が robots.txt で止められていないか。**門（common/kado.py）の判定をそのまま返す。**

    返り値は (許すか, 理由)。許すかは True・False・None（**確かめられなかった**）。
    robots.txt の実際の読み書き・判定（200・404/410・HTML・空 等）は
    common/kado.py がする。ここは寄せるだけで、モジュール側に判定を重複して持たない。
    **セッション（K.sesshon の中）でだけ意味がある。**
    """
    K = kado.genzai()
    if K is None:
        return None, "門（common/kado.py）が始まっていない"
    return K.robots_kekka(url)


def check_robots(url):
    """robots.txt が止めていないかを確かめる。止めている・確かめられないなら例外。

    **通っても「取ってよい」ではない。** 規約・公開条件は別の判断（共通仕様3.4）。
    **None（確かめられなかった）を許可として扱う分岐は無い。** fail-closed。
    **別の URL・別のホスト・http への言い換えで取り直さない**
    （門の対象host・URL範囲が、それを許さない）。
    """
    ok, why = robots_for(url)
    if ok is True:
        return
    if ok is False:
        raise RobotsDenied("robots_disallowed", why)
    raise RobotsUnknown("robots_unusable", why)


def get(url):
    """本体を1本取りに行く。(HTTP の状態, 中身) を返す。

    間隔（5秒）・URL範囲・相手台帳・robots.txt・転送のたびの関所は、
    門（common/kado.py。`urllib.request.urlopen()` の既定の opener）がする。
    ここでは 429・503 を Halted に変えるだけ（本体の1本として記録するため）。
    門で止まったときは `kado.Tomeru`（`urllib.error.URLError` の仲間）がそのまま出る。
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise Halted(e.code) from e
        raise


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    return sha256(path.read_bytes()) if path.is_file() else None


def rel(path):
    return path.relative_to(config.RAW).as_posix()


def write_atomic(path, data):
    """一時ファイルに書き切ってから置き換える。途中で落ちても前の版は壊れない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=PART)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def is_html(body):
    """先頭が HTML か。**CSV・ZIP のはずの本体にだけ使う。** robots.txt には使わない
    （robots.txt が HTML かどうかは common/kado.py が見る）。
    """
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html"))


def check_csv(body):
    """空と HTML は CSV ではない。取れたことにしない（前回の生データを守る）。

    空はその1本だけの失敗。HTML はその取得先ごと、この回は止める（UnexpectedHtml）。
    """
    if not body.strip():
        raise NotData("中身が空")
    if is_html(body):
        raise UnexpectedHtml("CSV")


def zip_members(data, city_code):
    """ZIP の中身を (名前, 大きさ, SHA-256) で返す。形が違えば NotData。

    展開先から外に出る名前（先頭の / 、\\ 、.. の段）は受け付けない。
    下の階層に入っているだけのものは受け付ける（配布元の ZIP の形を狭く決め打たない）。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            bad = z.testzip()
            if bad is not None:
                raise NotData(f"ZIP の {bad} が壊れている")
            members = []
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                    raise NotData(f"ZIP に展開先の外を指す名前がある: {name}")
                body = z.read(info)
                members.append({"name": name, "size": len(body), "sha256": sha256(body)})
    except zipfile.BadZipFile as e:
        raise NotData(f"ZIP として読めない: {e}") from e
    names = {m["name"] for m in members}
    need = {f"r2ka{city_code}.{ext}" for ext in ("shp", "shx", "dbf")}
    if not need <= names:
        raise NotData(f"ZIP に {sorted(need - names)} が無い")
    return sorted(members, key=lambda m: m["name"])


def content_sha256(members):
    """展開した中身の指紋。ZIP のバイト列ではなく、入っているファイルで決める。"""
    line = "".join(f"{m['name']}\0{m['sha256']}\n" for m in members)
    return sha256(line.encode("utf-8"))


def dir_members(path):
    if not path.is_dir():
        return None
    return sorted(({"name": p.relative_to(path).as_posix(), "size": p.stat().st_size,
                    "sha256": file_sha256(p)}
                   for p in path.rglob("*") if p.is_file()), key=lambda m: m["name"])


def replace_dir(dest, data):
    """ZIP を一時ディレクトリに展開してから、前の展開物と入れ替える。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=PART))
    old = dest.parent / f".{dest.name}.old{PART}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        if old.exists():
            shutil.rmtree(old)
        if dest.exists():
            dest.rename(old)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if old.exists() and not dest.exists():
            old.rename(dest)
        raise
    shutil.rmtree(old, ignore_errors=True)


def robots_entry(entry, prev_sha, e):
    """robots.txt で取らなかった1本の記録。本体には行っていない。"""
    return failed_entry(entry, prev_sha, e.status, str(e))


def gate_entry(entry, prev_sha, e):
    """門（common/kado.py）が、robots.txt の事前確認より先で止めた1本の記録
    （転送先が範囲の外・相手台帳に無い・この回この相手はもう止めた、等）。
    通信は出ていないか、途中（転送の1段目）までしか出ていない。
    """
    return failed_entry(entry, prev_sha, "gate_blocked", str(e))


def failed_entry(entry, prev_sha, status, err, http_status=None):
    entry.update({
        "status": status,
        "http_status": http_status,
        "error": err,
        "size": None,
        "sha256": None,
        "previous_sha256": prev_sha,
        "result": "kept_previous" if prev_sha else "missing",
    })
    return entry


def observe_csv(pref, year, teguchi):
    name = pref.csv_name(year, teguchi)
    out = config.RAW / name
    url = pref.csv_url(year, teguchi)
    prev = file_sha256(out)
    entry = {"kind": "police_csv", "url": url, "path": rel(out), "fetched_at": now()}
    # **本体の前に robots.txt。** 拒否・不明なら本体へ行かない
    try:
        check_robots(url)
    except RobotsStop as e:
        print(f"  取らない {name}  {e}")
        return robots_entry(entry, prev, e)
    try:
        status, body = get(url)
        check_csv(body)
    except Halted:
        raise
    except RobotsStop as e:     # 転送先で拒否・不明（門が転送のたびに確かめる）
        print(f"  取らない {name}  {e}")
        return robots_entry(entry, prev, e)
    except kado.Tomeru as e:    # 門のほかの理由で止めた（URL範囲の外・相手台帳 等）
        print(f"  取らない {name}  門で止めた：{e}")
        return gate_entry(entry, prev, e)
    except urllib.error.HTTPError as e:
        print(f"  失敗 {name}  HTTP {e.code}")
        return failed_entry(entry, prev, "failed", f"HTTP {e.code}", e.code)
    except NotData as e:
        print(f"  失敗 {name}  {e}")
        return failed_entry(entry, prev, "failed", str(e), 200)
    except Exception as e:
        print(f"  失敗 {name}  {e}")
        return failed_entry(entry, prev, "failed", f"{type(e).__name__}: {e}")

    new = sha256(body)
    if prev is None:
        result = "new"
    elif prev == new:
        result = "same"
    else:
        result = "changed"
    if result != "same":
        write_atomic(out, body)
    print(f"  取得 {name}  {len(body):,} バイト  {result}")
    entry.update({"status": "ok", "http_status": status, "error": None,
                  "size": len(body), "sha256": new, "previous_sha256": prev,
                  "result": result})
    return entry


def observe_boundary(c):
    label = f"境界_{c['name']}"
    dest = config.RAW / label
    zip_path = config.RAW / "source_zip" / f"{label}.zip"
    url = config.ESTAT_BOUNDARY.format(city_code=c["code"])
    prev_zip = file_sha256(zip_path)
    entry = {"kind": "estat_boundary_zip", "url": url, "path": rel(zip_path),
             "extracted_to": rel(dest), "fetched_at": now()}
    # **本体の前に robots.txt。** 拒否・不明なら本体へ行かない
    try:
        check_robots(url)
    except RobotsStop as e:
        print(f"  取らない {label}  {e}")
        return robots_entry(entry, prev_zip, e)
    try:
        status, data = get(url)
        if is_html(data):
            raise UnexpectedHtml("ZIP")
        members = zip_members(data, c["code"])
    except Halted:
        raise
    except RobotsStop as e:     # 転送先で拒否・不明
        print(f"  取らない {label}  {e}")
        return robots_entry(entry, prev_zip, e)
    except kado.Tomeru as e:    # 門のほかの理由で止めた
        print(f"  取らない {label}  門で止めた：{e}")
        return gate_entry(entry, prev_zip, e)
    except urllib.error.HTTPError as e:
        print(f"  失敗 {label}  HTTP {e.code}")
        return failed_entry(entry, prev_zip, "failed", f"HTTP {e.code}", e.code)
    except NotData as e:
        print(f"  失敗 {label}  {e}")
        return failed_entry(entry, prev_zip, "failed", str(e), 200)
    except Exception as e:
        print(f"  失敗 {label}  {e}")
        return failed_entry(entry, prev_zip, "failed", f"{type(e).__name__}: {e}")

    new_content = content_sha256(members)
    prev_content = None
    if zip_path.is_file():
        try:
            prev_content = content_sha256(zip_members(zip_path.read_bytes(), c["code"]))
        except NotData:
            prev_content = None   # 前の ZIP が読めないなら、今回ので置き換える
    if prev_zip is None:
        result = "new"
    elif prev_content == new_content:
        result = "same"
    else:
        result = "changed"
    if result != "same":
        write_atomic(zip_path, data)
    # 展開物は ZIP の中身と一致させる。同じ回でも、欠けていれば作り直す
    repaired = False
    if result != "same" or dir_members(dest) != members:
        replace_dir(dest, data)
        repaired = result == "same"
    print(f"  取得 {label}  {len(data):,} バイト  {result}")
    entry.update({"status": "ok", "http_status": status, "error": None,
                  "size": len(data), "sha256": sha256(data), "previous_sha256": prev_zip,
                  "result": result,
                  "zip_bytes_same": prev_zip == sha256(data),
                  "content_sha256": new_content,
                  "previous_content_sha256": prev_content,
                  "extracted_repaired": repaired,
                  "members": members})
    return entry


def not_attempted(entry, riyuu=None):
    """この回は行かなかった。理由が無ければ、既定の文言（前の1本で止めた）を使う。"""
    prev = file_sha256(config.RAW / entry["path"])
    return failed_entry(entry, prev, "not_attempted",
                        riyuu or "この回は行かなかった（同じ取得先の前の1本で止めた）")


def plan():
    """この回に取りに行くものの一覧。中止しても、行かなかったものを記録に残すため。"""
    csvs = [(pref, year, teguchi)
            for pref in config.PREFS.values()
            for year in config.YEARS
            for teguchi in config.TEGUCHI]
    return csvs, list(config.CITIES)


def observe_all(files, halted, K):
    """全部を取りに行き、manifest に書く中身を files と halted に積む。

    取得先（カード）ごとに、通信の前に **K.card_mon()**（門の部品）を見る。
    止める理由があれば、その並びは1本も通信せず、全部「取りに行かなかった」にする。
    通過したら、その並びの通信を全部 `with K.sesshon(...):` の中で行う。
    `kado.Tomeru` は門で止めた印（通信は出ていない）。捕まえて次の1本へ進む。

    途中で落ちても、そこまでの記録が manifest に残るように、渡された一覧に足していく。
    """
    csvs, cities = plan()

    print("県警CSV")
    riyuu = K.card_mon("hyogo-police-csv", hozon_saki=config.RAW)
    if riyuu:
        print(f"  門で止めた：{'／'.join(riyuu)}")
        for pref, year, teguchi in csvs:
            files.append(not_attempted({
                "kind": "police_csv", "url": pref.csv_url(year, teguchi),
                "path": pref.csv_name(year, teguchi), "fetched_at": None},
                "門で止めた：" + "／".join(riyuu)))
    else:
        with K.sesshon("hyogo-police-csv", hozon_saki=config.RAW):
            stop = None
            for pref, year, teguchi in csvs:
                if stop is not None:
                    files.append(not_attempted({
                        "kind": "police_csv", "url": pref.csv_url(year, teguchi),
                        "path": pref.csv_name(year, teguchi), "fetched_at": None},
                        str(stop)))
                    continue
                try:
                    files.append(observe_csv(pref, year, teguchi))
                except Halted as e:
                    print(f"  中止 {e}")
                    stop = e
                    name = pref.csv_name(year, teguchi)
                    files.append(failed_entry(
                        {"kind": "police_csv", "url": pref.csv_url(year, teguchi),
                         "path": name, "fetched_at": now()},
                        file_sha256(config.RAW / name), e.status, str(e), e.code))
                    halted.append({"host": "police", "http_status": e.code, "reason": e.status})

    print("町丁目境界")
    riyuu = K.card_mon("estat-boundary", hozon_saki=config.RAW)
    if riyuu:
        print(f"  門で止めた：{'／'.join(riyuu)}")
        for c in cities:
            label = f"境界_{c['name']}"
            files.append(not_attempted({
                "kind": "estat_boundary_zip",
                "url": config.ESTAT_BOUNDARY.format(city_code=c["code"]),
                "path": f"source_zip/{label}.zip", "extracted_to": label, "fetched_at": None},
                "門で止めた：" + "／".join(riyuu)))
    else:
        with K.sesshon("estat-boundary", hozon_saki=config.RAW):
            stop = None
            for c in cities:
                label = f"境界_{c['name']}"
                entry = {"kind": "estat_boundary_zip",
                         "url": config.ESTAT_BOUNDARY.format(city_code=c["code"]),
                         "path": f"source_zip/{label}.zip", "extracted_to": label}
                if stop is not None:
                    files.append(not_attempted(dict(entry, fetched_at=None), str(stop)))
                    continue
                try:
                    files.append(observe_boundary(c))
                except Halted as e:
                    print(f"  中止 {e}")
                    stop = e
                    files.append(failed_entry(dict(entry, fetched_at=now()),
                                              file_sha256(config.RAW / entry["path"]),
                                              e.status, str(e), e.code))
                    halted.append({"host": "e-stat", "http_status": e.code, "reason": e.status})


def summarize(files):
    out = {}
    for key in ("status", "result"):
        count = {}
        for f in files:
            count[f[key]] = count.get(f[key], 0) + 1
        out[key] = dict(sorted(count.items()))
    return out


def write_manifest(observed_at, files, halted, K, error=None):
    doc = {
        "observed_at": observed_at,
        "finished_at": now(),
        "tool": "scripts/fetch_data.py",
        "user_agent": config.USER_AGENT,
        "interval_seconds": config.REQUEST_INTERVAL,
        "run": {k: os.environ.get(v) for k, v in (
            ("repository", "GITHUB_REPOSITORY"),
            ("run_id", "GITHUB_RUN_ID"),
            ("commit", "GITHUB_SHA"))},
        "halted": halted,
        # 門（common/kado.py）が出した・止めた記録。robots.txt の全文は残さない
        "kado_kiroku": [{"card": cid, "url": url, "output": de, "reason": riyuu}
                        for cid, url, de, riyuu in K.kiroku],
        "error": error,
        "targets": len(files),
        "summary": summarize(files),
        "files": files,
    }
    # 1回の観測に1本。前の回の記録は上書きしない（同じ秒に2回走っても別の名前にする）
    stamp = observed_at.replace("-", "").replace(":", "")
    path = config.RAW / "manifest" / f"{stamp}.json"
    n = 2
    while path.exists():
        path = config.RAW / "manifest" / f"{stamp}-{n}.json"
        n += 1
    write_atomic(path, (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return path, doc


def kyou_jst():
    """この実行の日付（日本時間）。`RUN_DATE` があればそれ（読めない値なら門が落とす）。

    門（common/kado.py）は時計を見ないので、ここで1回決めて渡す。
    """
    return os.environ.get("RUN_DATE") or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()


def main():
    # 実行の最初に、門（common/kado.py）を1回だけ始める。以後の urlopen() は全部この門を通る
    K = kado.hajimeru(str(config.ROOT), config.SITE_ID, config.USER_AGENT, today=kyou_jst())
    config.RAW.mkdir(parents=True, exist_ok=True)
    observed_at = now()
    files, halted, error = [], [], None
    try:
        observe_all(files, halted, K)
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        path, doc = write_manifest(observed_at, files, halted, K, error)
        print(f"観測の記録  {rel(path)}  {doc['summary']}")
    ok = bool(files) and all(f["status"] == "ok" for f in files)
    print("完了" if ok else "取れなかったものがある。前回の生データは残した")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
