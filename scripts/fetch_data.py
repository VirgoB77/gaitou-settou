"""配布元から、県警CSVと e-Stat の町丁目境界を取ってくる。

取得の作法は共通仕様3.4に従う。
同時接続は1本、間隔は5秒以上、429/503 が返ったらその回は中止して次回に回す。
押し込まない。回り込まない。

**本体を取りに行く前に、そのホストの robots.txt を見る。** 拒否されていたら取らない。
robots.txt はホストごとに1回の実行で1度だけ取る（56本の CSV は同じホスト）。
転送（redirect）で別の場所へ行くときも、転送先を robots.txt で確かめる。
転送で拒否をすり抜けない。

robots.txt の結果（共通仕様3.4）:

    相手が答えた
      2xx        中身を読む。中身が止めていれば取らない
      404・410   置いていない。robots.txt の上では制限がない
      401・403   相手が robots.txt を見せない。取らない
      429・503   断られた。本体と同じく、その回は中止して次回に回す
      上のどれでもない（400・405・451、ほかの4xx・5xx）
                 **止めていないと確かめられない。この回は取らない。**
                 「通してよい」と決まっていない状態を、推測で通さない
    こちらから届かなかった（通信・中継・時間切れ）
                 **相手の答えではない。記録で分ける。** この回は取らない

**robots.txt が止めていないことは、取ってよいことを意味しない。**
規約・公開条件の判断は別（共通仕様3.4）。ここが見るのは robots.txt だけ。

**捕まえないもの。**

  ・待機列。HTML が返れば1本ずつ「CSV ではない」で失敗にするが、
    その回は続ける。待機列の画面かエラーページかを、機械では見分けていない
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
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

sys.stdout.reconfigure(encoding="utf-8")

# HTTPヘッダは ASCII しか通らない。日本語を入れると送信時に落ちる。
HEADERS = {"User-Agent": config.USER_AGENT}

# 作業中のファイルに付ける印。金庫の .gitignore が同じ印を外す。
PART = ".part"

_last_request = 0.0


class Halted(Exception):
    """断られたので、その回は打ち切る。"""

    def __init__(self, code):
        super().__init__(f"{code} が返りました。この回は中止します（次回に回す）")
        self.code = code


class NotData(Exception):
    """HTTP では成功したが、中身が期待した形ではない（エラーページなど）。"""


class RobotsStop(Exception):
    """robots.txt の都合で、本体へ行かない。回り込まない。

    status は観測の記録に書く名前。**相手の答えと、こちらの都合を分ける。**

      robots_disallowed   相手の robots.txt の中身が止めている
      robots_refused      相手が robots.txt を 401・403 で見せない
      robots_unusable     相手は答えたが、止めていないと確かめられない応答
      robots_unreachable  **こちらから届かなかった。相手の答えではない**
    """

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RobotsDenied(RobotsStop):
    """相手が止めている（中身・401・403）。"""


class RobotsUnknown(RobotsStop):
    """止めていないと確かめられない（相手の応答が根拠にならない／届かない）。"""


# この回に見た robots.txt。ホストごとに1つ。main() が回の頭で空にする。
#   キー  "https://ホスト"
#   値    (記録, 判定器)。判定器が None なら確かめられなかった
_robots = {}


def _pace():
    """前の要求から5秒以上あける。robots.txt も本体も、同じ時計で数える。"""
    global _last_request
    wait = config.REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _fetch_robots(url):
    """robots.txt を1本取る。(HTTP の状態, 中身) を返す。

    4xx・5xx は例外にせず状態で返す（判断は robots_for がする）。
    通信できないときだけ例外。robots.txt 自体は robots の対象外なので、
    転送はそのまま従う。やり直しはしない。
    """
    _pace()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, b""


def robots_for(url):
    """url のホストの robots.txt を、この回で1度だけ見る。(記録, 判定器)。"""
    parts = urllib.parse.urlsplit(url)
    key = f"{parts.scheme}://{parts.netloc}"
    if key in _robots:
        return _robots[key]
    robots_url = f"{key}/robots.txt"
    # reached … 相手が HTTP で答えたか。False はこちらの都合（通信・中継・時間切れ）
    rec = {"url": robots_url, "checked_at": now(), "reached": None, "http_status": None,
           "result": None, "sha256": None, "crawl_delay": None, "error": None}
    rp = urllib.robotparser.RobotFileParser(robots_url)
    try:
        status, body = _fetch_robots(robots_url)
    except Exception as e:
        # **こちらから届かなかった。** 中継が CONNECT を断った（403 を含む）ときもここ。
        # 相手の 403 とは記録を分ける（共通仕様3.4）
        rec.update(reached=False, result="unreachable", error=f"{type(e).__name__}: {e}")
        rp = None
    else:
        rec.update(reached=True, http_status=status)
        if status in (429, 503):
            rec["result"] = "halted"
            rp = None
        elif 200 <= status < 300:
            rp.parse(body.decode("utf-8", errors="replace").splitlines())
            rec.update(result="parsed", sha256=sha256(body),
                       crawl_delay=rp.crawl_delay(config.USER_AGENT))
        elif status in (404, 410):
            rp.allow_all = True
            rec["result"] = "not_found"
        elif status in (401, 403):
            rp.disallow_all = True
            rec["result"] = "refused"
        else:
            # 400・405・451、ほかの4xx・5xx。「通してよい」と決まっていない
            rec["result"] = "unusable"
            rp = None
    _robots[key] = (rec, rp)
    return _robots[key]


def check_robots(url):
    """robots.txt が止めていないかを確かめる。止めている・確かめられないなら例外。

    **通っても「取ってよい」ではない。** 規約・公開条件は別の判断（共通仕様3.4）。
    robots.txt に 429・503 → Halted（本体と同じ扱い。その回は中止）。
    **別の URL・別のホスト・http への言い換えで取り直さない。**
    """
    rec, rp = robots_for(url)
    r, code = rec["result"], rec["http_status"]
    if r == "halted":
        raise Halted(code)
    if r == "unreachable":
        raise RobotsUnknown("robots_unreachable",
                            f"{rec['url']} に届かなかった（こちら側：{rec['error']}）")
    if r == "unusable":
        raise RobotsUnknown("robots_unusable",
                            f"{rec['url']} が HTTP {code} を返した。止めていないと確かめられない")
    if r == "refused":
        raise RobotsDenied("robots_refused", f"{rec['url']} を相手が HTTP {code} で見せない")
    if not rp.can_fetch(config.USER_AGENT, url):
        raise RobotsDenied("robots_disallowed", f"{rec['url']} の中身が止めている")


class _RobotsRedirect(urllib.request.HTTPRedirectHandler):
    """転送先も robots.txt で確かめる。**転送で拒否をすり抜けない。**"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before = _last_request
        try:
            check_robots(newurl)
        except BaseException:
            fp.close()
            raise
        if _last_request != before:
            _pace()      # 転送先の robots.txt を取った直後に、本体へ行かない
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_RobotsRedirect)


def get(url):
    """1本ずつ、5秒以上あけて取りに行く。(HTTP の状態, 中身) を返す。

    先に robots.txt を確かめる（この回で見ていれば手元の判定だけで、通信はしない）。
    転送先も確かめる（_RobotsRedirect）。やり直しはしない。
    """
    check_robots(url)
    _pace()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with _opener.open(req, timeout=120) as r:
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


def check_csv(body):
    """空と HTML は CSV ではない。取れたことにしない（前回の生データを守る）。"""
    if not body.strip():
        raise NotData("中身が空")
    head = body[:512].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html")):
        raise NotData("CSV ではなく HTML が返った")


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
    # **本体の前に robots.txt。** 拒否・不明なら get を呼ばない
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
    except RobotsStop as e:     # 転送先で拒否・不明
        print(f"  取らない {name}  {e}")
        return robots_entry(entry, prev, e)
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
    # **本体の前に robots.txt。** 拒否・不明なら get を呼ばない
    try:
        check_robots(url)
    except RobotsStop as e:
        print(f"  取らない {label}  {e}")
        return robots_entry(entry, prev_zip, e)
    try:
        status, data = get(url)
        members = zip_members(data, c["code"])
    except Halted:
        raise
    except RobotsStop as e:     # 転送先で拒否・不明
        print(f"  取らない {label}  {e}")
        return robots_entry(entry, prev_zip, e)
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


def not_attempted(entry):
    prev = file_sha256(config.RAW / entry["path"])
    return failed_entry(entry, prev, "not_attempted", "この回は中止した（前の取得で断られた）")


def plan():
    """この回に取りに行くものの一覧。中止しても、行かなかったものを記録に残すため。"""
    csvs = [(pref, year, teguchi)
            for pref in config.PREFS.values()
            for year in config.YEARS
            for teguchi in config.TEGUCHI]
    return csvs, list(config.CITIES)


def observe_all(files, halted):
    """全部を取りに行き、manifest に書く中身を files と halted に積む。

    途中で落ちても、そこまでの記録が manifest に残るように、渡された一覧に足していく。
    """
    csvs, cities = plan()

    print("県警CSV")
    stop = None
    for pref, year, teguchi in csvs:
        if stop is not None:
            name = pref.csv_name(year, teguchi)
            files.append(not_attempted({
                "kind": "police_csv", "url": pref.csv_url(year, teguchi),
                "path": name, "fetched_at": None}))
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
                file_sha256(config.RAW / name), "halted", str(e), e.code))
            halted.append({"host": "police", "http_status": e.code})

    print("町丁目境界")
    stop = None
    for c in cities:
        label = f"境界_{c['name']}"
        entry = {"kind": "estat_boundary_zip",
                 "url": config.ESTAT_BOUNDARY.format(city_code=c["code"]),
                 "path": f"source_zip/{label}.zip", "extracted_to": label}
        if stop is not None:
            files.append(not_attempted(dict(entry, fetched_at=None)))
            continue
        try:
            files.append(observe_boundary(c))
        except Halted as e:
            print(f"  中止 {e}")
            stop = e
            files.append(failed_entry(dict(entry, fetched_at=now()),
                                      file_sha256(config.RAW / entry["path"]),
                                      "halted", str(e), e.code))
            halted.append({"host": "e-stat", "http_status": e.code})


def summarize(files):
    out = {}
    for key in ("status", "result"):
        count = {}
        for f in files:
            count[f[key]] = count.get(f[key], 0) + 1
        out[key] = dict(sorted(count.items()))
    return out


def write_manifest(observed_at, files, halted, error=None):
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
        # この回に見た robots.txt（ホストごとに1つ）。全文は残さず、指紋だけ
        "robots": [rec for rec, _ in _robots.values()],
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


def main():
    _robots.clear()   # robots.txt は回ごとに見直す。前の回の答えを持ち越さない
    config.RAW.mkdir(parents=True, exist_ok=True)
    observed_at = now()
    files, halted, error = [], [], None
    try:
        observe_all(files, halted)
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        path, doc = write_manifest(observed_at, files, halted, error)
        print(f"観測の記録  {rel(path)}  {doc['summary']}")
    ok = bool(files) and all(f["status"] == "ok" for f in files)
    print("完了" if ok else "取れなかったものがある。前回の生データは残した")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
