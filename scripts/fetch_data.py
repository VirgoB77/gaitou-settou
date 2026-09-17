"""配布元から、県警CSVと e-Stat の町丁目境界を取ってくる。

取得の作法は共通仕様3.4に従う。
同時接続は1本、間隔は5秒以上、429/503 が返ったらその回は中止して次回に回す。
押し込まない。回り込まない。
"""

import io
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

sys.stdout.reconfigure(encoding="utf-8")

# HTTPヘッダは ASCII しか通らない。日本語を入れると送信時に落ちる。
HEADERS = {"User-Agent": config.USER_AGENT}

_last_request = 0.0


class Halted(Exception):
    """断られたので、その回は打ち切る。"""


def get(url):
    """1本ずつ、5秒以上あけて取りに行く。"""
    global _last_request
    wait = config.REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise Halted(f"{e.code} が返りました。この回は中止します（次回に回す）")
        raise


def fetch_police():
    config.RAW.mkdir(parents=True, exist_ok=True)
    for pref in config.PREFS.values():
        for year in config.YEARS:
            for teguchi in config.TEGUCHI:
                name = pref.csv_name(year, teguchi)
                out = config.RAW / name
                if out.exists():
                    continue
                url = pref.csv_url(year, teguchi)
                try:
                    out.write_bytes(get(url))
                    print(f"  取得 {name}  {out.stat().st_size:,} バイト")
                except Halted as e:
                    print(f"  中止 {e}")
                    return
                except Exception as e:
                    print(f"  失敗 {name}  {e}")
                    print(f"       {url}")


def fetch_boundary():
    for c in config.CITIES:
        dest = config.RAW / f"境界_{c['name']}"
        if (dest / f"r2ka{c['code']}.shp").exists():
            continue
        url = config.ESTAT_BOUNDARY.format(city_code=c["code"])
        try:
            data = get(url)
        except Halted as e:
            print(f"  中止 {e}")
            return
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(dest)
        print(f"  取得 境界_{c['name']}  {len(data):,} バイト")


if __name__ == "__main__":
    print("県警CSV")
    fetch_police()
    print("町丁目境界")
    fetch_boundary()
    print("完了")
