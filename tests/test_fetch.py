#!/usr/bin/env python3
"""取得の段（scripts/fetch_data.py）を、配布元へ行かずに走らせる。

`fetch_data.get` を偽物に替えて、置き場（config.RAW）を一時ディレクトリにする。
見るものは、2026-09-23 に決めた取得の形そのもの。

  1. **在っても取りに行く。** 飛ばすと、配布元が同じ名前で差し替えたときに気づけない
  2. **同じなら生データに触らない。** 金庫に不要な差分を出さない
  3. **違えば置き換える。** 前の版は金庫の Git 履歴に残る
  4. **取れなかったら前回を壊さない。** 途中までのファイルも残さない
  5. **観測の記録は毎回残す。** 「見に行って同じだった」も記録
  6. **境界は ZIP そのものも残す。** 比べるのは展開した中身

  7. **本体の前に robots.txt を見る。転送先も見る。429/503 は中止する。
     拒否をすり抜けない。** これは門（common/kado.py）が持つ（TestGateWiring）

**通信の手前に門（common/kado.py）がある。** `Base` を継ぐ検査（1〜6の形を見るもの）は
「通す偽の門」（`TooruMon`）に差し替え、`fetch_data.get`・`fetch_data.check_robots` も
偽物にする。**門そのものの挙動は試していない**（読み取り・報告・ZIP・混雑処理など）ので、
本物の門を通す必要が無い。

門そのものの挙動（転送先の robots・429/503 の継続・拒否をすり抜けない）は
`TestGateWiring`・`TestGateBlocksBeforeAnyCommunication` が、一時フォルダに作った
承認つきのカード（tests/test_kado.py の `Oki` と同じ形）または未承認のカードで試す。
**本物の相手には1本も出さない。** 通信は偽の相手（`Nise`）が受けるか、
カードの門が通信そのものを止める。

**捕まえないもの。**

  ・本物の配布元の応答（URL の形・文字コード・ZIP の中身の実物）
  ・本物の robots.txt の中身。配布元がいま何を拒否しているかは、ここでは分からない
  ・common/kado.py 自身の robots.txt 判定の細かい分岐（200・404/410・HTML・空 等）。
    それは tests/test_kado.py が試す。ここで試すのは fetch_data.py の「寄せ方」だけ
  ・金庫への commit と push（workflow の段。test_workflow.py が並びだけ見る）
"""

import contextlib
import email.message
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
import fetch_data  # noqa: E402

kado = fetch_data.kado


def make_zip(code, body=b"shape", comment=b""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext in ("shp", "shx", "dbf", "prj"):
            z.writestr(f"r2ka{code}.{ext}", body + ext.encode())
        z.comment = comment
    return buf.getvalue()


class Fake:
    """URL ごとに返すものを決める。呼ばれた URL は calls に残す。

    `fetch_data.get` そのものを差し替える。門は「通す偽の門」（TooruMon）が
    バイパスしているので、ここでは robots.txt を意識しない。
    """

    def __init__(self):
        self.calls = []
        self.override = {}

    def default(self, url):
        for c in config.CITIES:
            if url == config.ESTAT_BOUNDARY.format(city_code=c["code"]):
                return make_zip(c["code"])
        return f"罪名,町丁目\n窃盗,{url}\n".encode("utf-8")

    def __call__(self, url):
        self.calls.append(url)
        v = self.override.get(url)
        if isinstance(v, BaseException):
            raise v
        return 200, (v if v is not None else self.default(url))


class NoNetwork(AssertionError):
    """ソケットを開こうとした。偽物にし忘れた通信がある。"""


def _no_network(*a, **k):
    raise NoNetwork(f"外へ繋ごうとした: {a[:1]}")


class TooruMon:
    """**通す偽の門。** `card_mon` はいつも通す。robots.txt にも本体にも自分では出ない
    （`fetch_data.get` と `fetch_data.check_robots` は別に偽物へ差し替える）。

    fetch_data 自身の中身（manifest・ZIP・同じ／違う の判定・混雑処理）を試すためだけの
    道具。**本物のコードに「検査のときは通す」道は作っていない**——差し替えるのは
    検査の中のこのクラスだけ。本物の門（common/kado.py）の挙動そのものは
    `TestGateWiring` のほうで、本物の `kado.Kado` を使って試す。
    """

    def __init__(self):
        self.kiroku = []

    def card_mon(self, cid, hozon_saki=None, **kw):
        return []

    @contextlib.contextmanager
    def sesshon(self, cid, hozon_saki=None, **kw):
        yield self


def csv_targets():
    return [(p.csv_name(y, t), p.csv_url(y, t))
            for p in config.PREFS.values() for y in config.YEARS for t in config.TEGUCHI]


def zip_url(city):
    return config.ESTAT_BOUNDARY.format(city_code=city["code"])


class Base(unittest.TestCase):
    """1〜6の形（門そのものの挙動ではないもの）を試す。「通す偽の門」を差し込む。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        self.fake = Fake()
        for p in (mock.patch.object(config, "RAW", self.raw),
                  mock.patch.object(fetch_data, "get", self.fake),
                  mock.patch.object(fetch_data, "check_robots", lambda url: None),
                  mock.patch.object(fetch_data.kado, "hajimeru",
                                    lambda *a, **k: TooruMon()),
                  # 偽物にし忘れた通信は、外へ出る前にここで落ちる
                  mock.patch.object(socket, "create_connection", _no_network),
                  mock.patch.object(socket.socket, "connect", _no_network)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.n_targets = len(csv_targets()) + len(config.CITIES)

    def hosts_called(self):
        """本体の取得関数が呼ばれたホスト。"""
        return {u.split("/")[2] for u in self.fake.calls}

    def manifests(self):
        return set((self.raw / "manifest").glob("*.json"))

    def run_once(self):
        before = self.manifests()
        with mock.patch("sys.stdout", new=io.StringIO()):
            code = fetch_data.main()
        new = self.manifests() - before
        self.assertEqual(len(new), 1, "1回の観測で、記録がちょうど1本増える")
        return code, json.loads(new.pop().read_text(encoding="utf-8"))

    def entry(self, doc, path):
        return next(f for f in doc["files"] if f["path"] == path)

    def part_files(self):
        return [p for p in self.raw.rglob("*") if p.name.endswith(fetch_data.PART)]


class TestAlwaysFetch(Base):
    def test_fetches_everything_even_if_present(self):
        code, _ = self.run_once()
        self.assertEqual(code, 0)
        first = len(self.fake.calls)
        self.assertEqual(first, self.n_targets)
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake.calls) - first, self.n_targets,
                         "2回目も全部に取りに行くこと（在るから飛ばす、をしない）")
        self.assertEqual(doc["summary"]["result"], {"same": self.n_targets})

    def test_same_content_is_not_rewritten(self):
        self.run_once()
        name, _ = csv_targets()[0]
        before = (self.raw / name).stat().st_mtime_ns
        zip_before = (self.raw / "source_zip" / f"境界_{config.CITIES[0]['name']}.zip").stat().st_mtime_ns
        self.run_once()
        self.assertEqual((self.raw / name).stat().st_mtime_ns, before)
        self.assertEqual((self.raw / "source_zip" / f"境界_{config.CITIES[0]['name']}.zip")
                         .stat().st_mtime_ns, zip_before)

    def test_changed_content_replaces_same_path(self):
        self.run_once()
        name, url = csv_targets()[3]
        old = (self.raw / name).read_bytes()
        self.fake.override[url] = b"new,content\n1,2\n"
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        self.assertEqual((self.raw / name).read_bytes(), b"new,content\n1,2\n")
        e = self.entry(doc, name)
        self.assertEqual(e["result"], "changed")
        self.assertEqual(e["previous_sha256"], fetch_data.sha256(old))
        self.assertEqual(e["sha256"], fetch_data.sha256(b"new,content\n1,2\n"))


class TestFailureKeepsPrevious(Base):
    def test_http_error_keeps_previous(self):
        self.run_once()
        name, url = csv_targets()[5]
        old = (self.raw / name).read_bytes()
        self.fake.override[url] = urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        code, doc = self.run_once()
        self.assertEqual(code, 1, "取れなかったものがあれば落ちる（公開に進まない）")
        self.assertEqual((self.raw / name).read_bytes(), old)
        e = self.entry(doc, name)
        self.assertEqual((e["status"], e["http_status"], e["result"]), ("failed", 404, "kept_previous"))
        self.assertEqual(e["previous_sha256"], fetch_data.sha256(old))
        self.assertEqual(self.part_files(), [])

    def test_html_page_is_not_data(self):
        self.run_once()
        name, url = csv_targets()[7]
        old = (self.raw / name).read_bytes()
        self.fake.override[url] = b"<!DOCTYPE html><html>maintenance</html>"
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        self.assertEqual((self.raw / name).read_bytes(), old)
        self.assertEqual(self.entry(doc, name)["result"], "kept_previous")

    def test_first_time_failure_is_missing(self):
        name, url = csv_targets()[0]
        self.fake.override[url] = urllib.error.URLError("接続できない")
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        self.assertFalse((self.raw / name).exists())
        self.assertEqual(self.entry(doc, name)["result"], "missing")

    def test_halted_stops_that_host_and_records_the_rest(self):
        self.run_once()
        targets = csv_targets()
        name, url = targets[2]
        self.fake.override[url] = fetch_data.Halted(503)
        calls_before = len(self.fake.calls)
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        made = self.fake.calls[calls_before:]
        self.assertNotIn(targets[3][1], made, "断られたら、同じ相手の残りには行かない")
        for c in config.CITIES:
            self.assertIn(zip_url(c), made, "別の相手（e-Stat）には行く")
        self.assertEqual(self.entry(doc, name)["status"], "halted")
        rest = [self.entry(doc, n) for n, _ in targets[3:]]
        self.assertTrue(all(e["status"] == "not_attempted" for e in rest))
        self.assertTrue(all(e["result"] == "kept_previous" for e in rest))
        self.assertEqual(doc["targets"], self.n_targets, "行かなかったものも記録に並ぶ")

    def test_broken_zip_keeps_previous_zip_and_extracted(self):
        self.run_once()
        c = config.CITIES[1]
        zpath = self.raw / "source_zip" / f"境界_{c['name']}.zip"
        old_zip = zpath.read_bytes()
        shp = self.raw / f"境界_{c['name']}" / f"r2ka{c['code']}.shp"
        old_shp = shp.read_bytes()
        self.fake.override[zip_url(c)] = b"PK\x03\x04 broken"
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        self.assertEqual(zpath.read_bytes(), old_zip)
        self.assertEqual(shp.read_bytes(), old_shp)
        self.assertEqual(self.entry(doc, f"source_zip/境界_{c['name']}.zip")["result"], "kept_previous")
        self.assertEqual(self.part_files(), [])

    def test_zip_with_path_outside_is_refused(self):
        c = config.CITIES[0]
        for bad in (f"../r2ka{c['code']}.shp", f"/tmp/r2ka{c['code']}.shp", f"a/../../r2ka{c['code']}.shp"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for ext in ("shp", "shx", "dbf"):
                    z.writestr(f"r2ka{c['code']}.{ext}", b"x")
                z.writestr(bad, b"x")
            self.fake.override[zip_url(c)] = buf.getvalue()
            code, doc = self.run_once()
            self.assertEqual(code, 1, bad)
            self.assertEqual(self.entry(doc, f"source_zip/境界_{c['name']}.zip")["status"], "failed")
        self.assertFalse((self.raw.parent / f"r2ka{c['code']}.shp").exists())


class TestBoundaryZipShape(Base):
    def test_files_in_a_subfolder_are_accepted(self):
        """配布元の ZIP に下の階層があっても、止めない（展開して比べる）。"""
        c = config.CITIES[0]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for ext in ("shp", "shx", "dbf"):
                z.writestr(f"r2ka{c['code']}.{ext}", b"x" + ext.encode())
            z.writestr("doc/readme.txt", b"note")
        self.fake.override[zip_url(c)] = buf.getvalue()
        code, _ = self.run_once()
        self.assertEqual(code, 0)
        self.assertTrue((self.raw / f"境界_{c['name']}" / "doc" / "readme.txt").is_file())
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        e = self.entry(doc, f"source_zip/境界_{c['name']}.zip")
        self.assertEqual((e["result"], e["extracted_repaired"]), ("same", False))


class TestBoundaryZip(Base):
    def test_zip_itself_and_extracted_files_are_kept(self):
        self.run_once()
        for c in config.CITIES:
            self.assertTrue((self.raw / "source_zip" / f"境界_{c['name']}.zip").is_file())
            for ext in ("shp", "shx", "dbf", "prj"):
                self.assertTrue((self.raw / f"境界_{c['name']}" / f"r2ka{c['code']}.{ext}").is_file())

    def test_zip_bytes_differ_but_content_same(self):
        """ZIP のバイト列だけが違い、展開した中身は同じ。

        2026-09-18 と 09-21 で、境界 ZIP の大きさが1バイトずつ違った（中身は未確認）。
        """
        self.run_once()
        c = config.CITIES[0]
        zpath = self.raw / "source_zip" / f"境界_{c['name']}.zip"
        old = zpath.read_bytes()
        self.fake.override[zip_url(c)] = make_zip(c["code"], comment=b"x")
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(zpath.read_bytes(), old, "中身が同じなら ZIP を書き換えない")
        e = self.entry(doc, f"source_zip/境界_{c['name']}.zip")
        self.assertEqual(e["result"], "same")
        self.assertFalse(e["zip_bytes_same"])
        self.assertNotEqual(e["sha256"], e["previous_sha256"], "今回のバイト列の指紋は記録に残る")

    def test_zip_content_changed_replaces_both(self):
        self.run_once()
        c = config.CITIES[1]
        self.fake.override[zip_url(c)] = make_zip(c["code"], body=b"moved")
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        shp = self.raw / f"境界_{c['name']}" / f"r2ka{c['code']}.shp"
        self.assertEqual(shp.read_bytes(), b"movedshp")
        self.assertEqual(self.entry(doc, f"source_zip/境界_{c['name']}.zip")["result"], "changed")

    def test_missing_extracted_files_are_restored(self):
        self.run_once()
        c = config.CITIES[0]
        (self.raw / f"境界_{c['name']}" / f"r2ka{c['code']}.dbf").unlink()
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        self.assertTrue((self.raw / f"境界_{c['name']}" / f"r2ka{c['code']}.dbf").is_file())
        self.assertTrue(self.entry(doc, f"source_zip/境界_{c['name']}.zip")["extracted_repaired"])


class TestManifest(Base):
    KEYS = {"kind", "url", "path", "fetched_at", "status", "http_status",
            "error", "size", "sha256", "previous_sha256", "result"}

    def test_every_run_leaves_its_own_record(self):
        self.run_once()
        self.run_once()
        with mock.patch.object(fetch_data, "now", return_value="2099-01-01T00:00:00Z"):
            self.run_once()
            self.run_once()
        self.assertEqual(len(self.manifests()), 4,
                         "同じ中身でも、見に行ったことは毎回残る（同じ秒でも上書きしない）")

    def test_record_has_what_we_need_later(self):
        _, doc = self.run_once()
        self.assertEqual(doc["targets"], self.n_targets)
        self.assertEqual(len(doc["files"]), self.n_targets)
        for e in doc["files"]:
            self.assertTrue(self.KEYS <= set(e), f"欄が足りない: {self.KEYS - set(e)}")
            self.assertEqual((e["status"], e["http_status"]), ("ok", 200))
            self.assertEqual(e["size"], (self.raw / e["path"]).stat().st_size)
            self.assertEqual(e["sha256"], fetch_data.file_sha256(self.raw / e["path"]))
        self.assertIn("observed_at", doc)
        self.assertIn("kado_kiroku", doc, "門（common/kado.py）が出した・止めた記録を残す欄")
        self.assertEqual(doc["user_agent"], config.USER_AGENT)

    def test_record_is_written_even_when_it_crashes(self):
        with mock.patch.object(fetch_data, "plan", side_effect=RuntimeError("想定外")):
            with mock.patch("sys.stdout", new=io.StringIO()):
                with self.assertRaises(RuntimeError):
                    fetch_data.main()
        docs = list((self.raw / "manifest").glob("*.json"))
        self.assertEqual(len(docs), 1)
        self.assertIn("想定外", json.loads(docs[0].read_text(encoding="utf-8"))["error"])


class TestUnexpectedHtml(Base):
    """CSV・ZIP のはずの所に HTML が返ったら、その取得先にはその回もう行かない。

    何の画面か（待機列・エラーページ・ログイン画面）は決めつけない。
    見たこと（HTML が返った）だけを記録する。**これは門ではなく fetch_data 自身の判断**
    （門は CSV・ZIP の中身までは知らない）。

    **捕まえないもの。** HTML の中身が何を意味するか。見分けていない。
    """

    def hyogo_calls(self):
        return [u for u in self.fake.calls if "web.pref.hyogo.lg.jp" in u]

    def estat_calls(self):
        return [u for u in self.fake.calls if "www.e-stat.go.jp" in u]

    # 1. CSV のはずが HTML → 同じ取得先の次の CSV へ行かない
    def test_csv_html_stops_that_host(self):
        targets = csv_targets()
        name, url = targets[4]
        self.fake.override[url] = b"<!DOCTYPE html><html><body>...</body></html>"
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        self.assertEqual(self.hyogo_calls(), [u for _, u in targets[:5]],
                         "HTML が返った後も、同じ取得先の CSV を取りに行った")
        self.assertEqual(self.entry(doc, name)["status"], "unexpected_html")
        rest = [self.entry(doc, n) for n, _ in targets[5:]]
        self.assertEqual({e["status"] for e in rest}, {"not_attempted"})

    # 2. ZIP のはずが HTML → 同じ取得先の次へ行かない
    def test_zip_html_stops_that_host(self):
        first = config.CITIES[0]
        self.fake.override[zip_url(first)] = b"<!DOCTYPE html><html><body>...</body></html>"
        code, doc = self.run_once()
        self.assertEqual(code, 1)
        self.assertEqual(self.estat_calls(), [zip_url(first)],
                         "HTML が返った後も、同じ取得先の ZIP を取りに行った")
        path = f"source_zip/境界_{first['name']}.zip"
        self.assertEqual(self.entry(doc, path)["status"], "unexpected_html")
        for c in config.CITIES[1:]:
            self.assertEqual(self.entry(doc, f"source_zip/境界_{c['name']}.zip")["status"],
                             "not_attempted")

    # 3. 取得先 A が HTML でも、取得先 B は普通に進む
    def test_other_host_goes_on(self):
        self.fake.override[csv_targets()[0][1]] = b"<!DOCTYPE html><html><body>...</body></html>"
        code, doc = self.run_once()
        self.assertEqual(len(self.hyogo_calls()), 1)
        self.assertEqual(self.estat_calls(), [zip_url(c) for c in config.CITIES],
                         "別の取得先まで止めた")
        zips = [f for f in doc["files"] if f["kind"] == "estat_boundary_zip"]
        self.assertEqual({f["status"] for f in zips}, {"ok"})

    def test_zip_host_html_does_not_stop_csv_host(self):
        self.fake.override[zip_url(config.CITIES[0])] = b"<!DOCTYPE html><html><body>...</body></html>"
        self.run_once()
        self.assertEqual(len(self.hyogo_calls()), len(csv_targets()))

    # 4. 普通の CSV・ZIP では止まらない
    def test_normal_data_does_not_stop(self):
        code, doc = self.run_once()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake.calls), self.n_targets)
        self.assertEqual(doc["halted"], [])

    def test_empty_csv_is_one_failure_not_a_stop(self):
        """HTML でない失敗（空）は、その1本だけ。取得先ごとは止めない。"""
        targets = csv_targets()
        self.fake.override[targets[2][1]] = b""
        self.run_once()
        self.assertEqual(len(self.hyogo_calls()), len(targets))

    # 5. 「待機列」とは書かない。見たことだけ
    def test_records_only_what_was_seen(self):
        name, url = csv_targets()[1]
        self.fake.override[url] = b"<!DOCTYPE html><html><body>...</body></html>"
        _, doc = self.run_once()
        e = self.entry(doc, name)
        self.assertEqual((e["status"], e["http_status"]), ("unexpected_html", 200))
        self.assertEqual(doc["halted"], [{"host": "police", "http_status": 200,
                                          "reason": "unexpected_html"}])
        text = json.dumps(doc, ensure_ascii=False).lower()
        for guess in ("waiting", "queue", "待機", "login", "ログイン", "maintenance"):
            self.assertNotIn(guess, text, f"記録が理由を推測している: {guess}")
        rest = [f for f in doc["files"] if f["status"] == "not_attempted"]
        self.assertTrue(rest)
        self.assertTrue(all("断られた" not in f["error"] for f in rest),
                        "HTML は断られたのではない")

    # 6. やり直さない
    def test_html_is_not_retried(self):
        name, url = csv_targets()[3]
        self.fake.override[url] = b"<!DOCTYPE html><html><body>...</body></html>"
        self.run_once()
        self.assertEqual(self.fake.calls.count(url), 1)

    def test_previous_data_is_kept(self):
        self.run_once()
        targets = csv_targets()
        before = {n: (self.raw / n).read_bytes() for n, _ in targets}
        self.fake.override[targets[0][1]] = b"<!DOCTYPE html><html><body>...</body></html>"
        self.run_once()
        self.assertEqual({n: (self.raw / n).read_bytes() for n, _ in targets}, before,
                         "HTML の回に、前回の生データを書き換えた")


# ---------------------------------------------------------------------------
# 門そのものの挙動（common/kado.py への寄せ方）。本物の Kado を使う。
# tests/test_kado.py の Oki・Nise と同じ形。**本物の相手には1本も出さない。**
# ---------------------------------------------------------------------------

GHOST = "www.example.lg.jp"      # 偽の相手（本物のホストには行かない）
GHOST2 = "cdn.example.lg.jp"     # 転送先用に、同じ相手のもう1つの host
GURL = f"https://{GHOST}/data/list.csv"
UA = "kujiraya archive bot (+https://example.invalid/about; https://example.invalid/form)"
REPO = config.SITE_ID


class Nise(urllib.request.BaseHandler):
    """偽の相手。URL ごとに (status, headers, body) を返す。来た URL は控える。

    tests/test_kado.py の Nise と同じ形（本物の kado.Kado に差し込む transport）。
    """
    handler_order = 50

    def __init__(self, kotae):
        self.kotae = kotae
        self.kita = []

    def _open(self, req):
        self.kita.append(req.full_url)
        status, headers, body = self.kotae.get(req.full_url, (404, {}, b""))
        if isinstance(status, BaseException):
            raise status
        msg = email.message.Message()
        for k, v in headers.items():
            msg[k] = v
        r = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, status)
        r.msg = "nise"
        return r

    https_open = _open
    http_open = _open


ROBOTS_OK = (200, {"Content-Type": "text/plain"}, b"User-agent: *\nAllow: /\n")


def yoi_card(**kae):
    c = {
        "取得元": "ためしの一覧", "相手": "ためし県", "source種別": "行政",
        "対象URL": GURL, "対象host": [GHOST, GHOST2],
        "個人情報を含みうる": "分からない", "当事者に個人がありうる": "分からない",
        "個票の粒度": "未確認", "所在地の扱い": "未確認",
        "規約確認日": "2026-09-20",
        "規約証跡": {"規約URL": f"https://{GHOST}/kiyaku.html"},
        "正規提供手段": "無し", "正規提供手段の理由": "API も CSV も無い",
        "承認対象の行為": ["自動取得", "内部保存"],
        "承認する取得方法": {"URL範囲": [f"https://{GHOST}/data/", f"https://{GHOST2}/data/"],
                         "対象種類": "CSV", "ページ送り・深さ": "1段",
                         "API/feed": "なし", "承認頻度": "1日1回"},
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


class GateBase(unittest.TestCase):
    """一時フォルダに、承認つきのカード・相手台帳・金庫を置く（tests/test_kado.py の Oki と同じ形）。

    **本物の相手には1本も出さない。** 通信は偽の相手（Nise）が受ける。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.kinko = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "data", "ref", "shounin"))
        self.cards = {"tameshi": yoi_card()}
        self.daicho = {"aite": {"ためし県": {"host": [GHOST, GHOST2], "担当": REPO}}}
        self.env = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1", "RUN_DATE": "2026-09-25"}
        self._kaku_all()
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "はじめ")
        self._shounin()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.kinko, True)
        self.addCleanup(urllib.request.install_opener, None)

    def _p(self, *a):
        return os.path.join(self.root, *a)

    def _kaku(self, rel, d):
        with open(self._p(rel), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def _kaku_all(self):
        self._kaku("data/ref/torimoto-card.json", {"cards": self.cards})
        self._kaku("data/ref/aite-daicho.json", self.daicho)

    def _shounin(self, cid="tameshi"):
        card = self.cards[cid]
        s = {"カード": cid, "運営者承認": "承認", "承認したカード版": card["カード版"],
             "承認時カード指紋": kado.card_shimon(card), "承認日": "2026-09-24"}
        self._kaku("data/ref/shounin/%s.json" % cid, s)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "承認")

    def mon(self, kotae):
        self.nise = Nise(kotae)
        self.naps = []
        return kado.Kado(self.root, REPO, UA, env=self.env, transport=self.nise,
                         run_id="run-1", sleep=self.naps.append, now=lambda: 1000.0)

    def kinko_raw(self):
        return os.path.join(self.kinko, "raw")


class TestGateWiring(GateBase):
    """fetch_data.check_robots / fetch_data.get が、本物の門を正しく使っているかを試す。

    common/kado.py 自身の robots.txt 判定の細かい分岐は tests/test_kado.py の分担。
    ここで見るのは「fetch_data がその判定を回り込んでいないか」。
    """

    def test_direct_disallow_is_not_bypassed(self):
        kotae = {f"https://{GHOST}/robots.txt": (
            200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /data/\n")}
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            with self.assertRaises(fetch_data.RobotsDenied):
                fetch_data.check_robots(GURL)
            with self.assertRaises(kado.Tomeru):
                fetch_data.get(GURL)
        self.assertNotIn(GURL, self.nise.kita, "拒否されているのに本体へ出した")

    def test_unclear_robots_is_not_treated_as_allowed(self):
        """確かめられなかった（robots.txt が HTML など）を許可として扱わない。fail-closed。"""
        kotae = {f"https://{GHOST}/robots.txt": (200, {"Content-Type": "text/html"},
                                                 b"<html></html>")}
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            with self.assertRaises(fetch_data.RobotsUnknown):
                fetch_data.check_robots(GURL)
            with self.assertRaises(kado.Tomeru):
                fetch_data.get(GURL)
        self.assertNotIn(GURL, self.nise.kita)

    def test_allowed_reaches_the_body(self):
        kotae = {f"https://{GHOST}/robots.txt": ROBOTS_OK, GURL: (200, {}, b"ok,1\n")}
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            fetch_data.check_robots(GURL)      # 例外なし
            status, body = fetch_data.get(GURL)
        self.assertEqual((status, body), (200, b"ok,1\n"))
        self.assertEqual(self.nise.kita, [f"https://{GHOST}/robots.txt", GURL])

    def test_redirect_target_robots_is_checked_and_not_bypassed(self):
        """転送先も robots.txt で確かめる。**転送で拒否をすり抜けない。**"""
        moved = f"https://{GHOST2}/data/x.csv"
        kotae = {
            f"https://{GHOST}/robots.txt": ROBOTS_OK,
            GURL: (302, {"Location": moved}, b""),
            f"https://{GHOST2}/robots.txt": (200, {"Content-Type": "text/plain"},
                                             b"User-agent: *\nDisallow: /\n"),
        }
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            with self.assertRaises(kado.Tomeru):
                fetch_data.get(GURL)
        self.assertIn(f"https://{GHOST2}/robots.txt", self.nise.kita,
                     "転送先の robots.txt を見ていない")
        self.assertNotIn(moved, self.nise.kita, "転送先が拒否しているのに本体へ出した")

    def test_redirect_target_allowed_is_followed(self):
        """転送先が許していれば、そこまで読む（転送そのものを止めているのではない）。"""
        moved = f"https://{GHOST2}/data/x.csv"
        kotae = {
            f"https://{GHOST}/robots.txt": ROBOTS_OK,
            GURL: (302, {"Location": moved}, b""),
            f"https://{GHOST2}/robots.txt": ROBOTS_OK,
            moved: (200, {}, b"moved,1\n"),
        }
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            status, body = fetch_data.get(GURL)
        self.assertEqual((status, body), (200, b"moved,1\n"))
        self.assertIn(moved, self.nise.kita)

    def test_429_halts_and_the_gate_stops_the_rest_of_this_run(self):
        """429/503 は Halted（本体の1本として記録）。同じ相手への次の1本は、
        門が自動で止める（`kado.Tomeru`。通信は出ない）。"""
        other = f"https://{GHOST}/data/other.csv"
        kotae = {f"https://{GHOST}/robots.txt": ROBOTS_OK, GURL: (429, {}, b"")}
        K = self.mon(kotae).install()
        with K.sesshon("tameshi", hozon_saki=self.kinko_raw()):
            with self.assertRaises(fetch_data.Halted):
                fetch_data.get(GURL)
            calls_before = len(self.nise.kita)
            with self.assertRaises(kado.Tomeru):
                fetch_data.get(other)
        self.assertEqual(len(self.nise.kita), calls_before,
                         "止めたあとの1本は、通信を出さずに門で止めた")


class TestGateBlocksBeforeAnyCommunication(unittest.TestCase):
    """統括判定案が無い（未承認の）カードなら、`observe_all` は1本も通信しない。

    **実物の data/ref は読まない。** 将来そこが承認されても、この検査の前提は
    崩れないように、一時フォルダに未承認のカードを自分で作る。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.kinko = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "data", "ref", "shounin"))
        cards = {
            "hyogo-police-csv": {"統括判定案": "", "相手": "ためし県", "対象host": [],
                                 "承認する取得方法": {"URL範囲": []}, "承認対象の行為": []},
            "estat-boundary": {"統括判定案": "", "相手": "ためし総省", "対象host": [],
                               "承認する取得方法": {"URL範囲": []}, "承認対象の行為": []},
        }
        with open(os.path.join(self.root, "data", "ref", "torimoto-card.json"),
                 "w", encoding="utf-8") as f:
            json.dump({"cards": cards}, f)
        with open(os.path.join(self.root, "data", "ref", "aite-daicho.json"),
                 "w", encoding="utf-8") as f:
            json.dump({"aite": {}}, f)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.kinko, True)

    def test_no_network_and_everything_not_attempted(self):
        env = {"KINKO_DIR": self.kinko, "KINKO_PRIVATE": "1", "RUN_DATE": "2026-09-25"}
        K = kado.Kado(self.root, config.SITE_ID, config.USER_AGENT, env=env,
                      transport=_RaisesIfOpened())
        files, halted = [], []
        fetch_data.observe_all(files, halted, K)
        self.assertTrue(files)
        self.assertEqual({f["status"] for f in files}, {"not_attempted"})
        self.assertEqual(halted, [])
        self.assertEqual(len(files), self.expected_count())

    def expected_count(self):
        csvs, cities = fetch_data.plan()
        return len(csvs) + len(cities)


class _RaisesIfOpened(urllib.request.BaseHandler):
    """通信しようとしたら落ちる。`card_mon` が先に止めるはずなので、呼ばれないはず。"""
    handler_order = 10

    def http_open(self, req):
        raise AssertionError(f"門を通り抜けて通信しようとした: {req.full_url}")

    https_open = http_open


if __name__ == "__main__":
    unittest.main(verbosity=2)
