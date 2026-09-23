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

**捕まえないもの。**

  ・本物の配布元の応答（URL の形・文字コード・ZIP の中身の実物）
  ・金庫への commit と push（workflow の段。test_workflow.py が並びだけ見る）
"""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config  # noqa: E402
import fetch_data  # noqa: E402


def make_zip(code, body=b"shape", comment=b""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext in ("shp", "shx", "dbf", "prj"):
            z.writestr(f"r2ka{code}.{ext}", body + ext.encode())
        z.comment = comment
    return buf.getvalue()


class Fake:
    """URL ごとに返すものを決める。呼ばれた URL は calls に残す。"""

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


def csv_targets():
    return [(p.csv_name(y, t), p.csv_url(y, t))
            for p in config.PREFS.values() for y in config.YEARS for t in config.TEGUCHI]


def zip_url(city):
    return config.ESTAT_BOUNDARY.format(city_code=city["code"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        self.fake = Fake()
        for p in (mock.patch.object(config, "RAW", self.raw),
                  mock.patch.object(fetch_data, "get", self.fake)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.n_targets = len(csv_targets()) + len(config.CITIES)

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
        self.assertEqual(doc["user_agent"], config.USER_AGENT)

    def test_record_is_written_even_when_it_crashes(self):
        with mock.patch.object(fetch_data, "plan", side_effect=RuntimeError("想定外")):
            with mock.patch("sys.stdout", new=io.StringIO()):
                with self.assertRaises(RuntimeError):
                    fetch_data.main()
        docs = list((self.raw / "manifest").glob("*.json"))
        self.assertEqual(len(docs), 1)
        self.assertIn("想定外", json.loads(docs[0].read_text(encoding="utf-8"))["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
