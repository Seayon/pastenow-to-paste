"""Artificial fixtures only. Integration uses the user's installed model, never their stores."""
import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate as m

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=")
APP = Path(os.environ.get("PASTE_APP", "/Applications/Paste.app"))


def source_fixture(directory):
    directory.mkdir()
    connection = sqlite3.connect(directory / "PasteNow.sqlite")
    connection.executescript("""
        create table Z_METADATA(Z_UUID text);
        insert into Z_METADATA values('synthetic-source-uuid');
        create table ZAPPLICATION(Z_PK integer,ZBUNDLEID text,ZNAME text,ZICON blob);
        insert into ZAPPLICATION values(1,'org.example.synthetic','Synthetic App',null);
        create table ZPASTELIST(Z_PK integer,ZNAME text,ZLISTTYPE integer,ZSORTWEIGHT integer);
        insert into ZPASTELIST values(1,'All',-1,0);
        insert into ZPASTELIST values(2,'人工分类',0,1);
        create table ZPASTEITEMDATA(Z_PK integer,ZDATA blob,ZCOMPRESSED integer);
        create table ZPASTEITEM(Z_PK integer,ZITEMDATA integer,ZTIMESTAMP real,
          ZUPDATEDATE real,ZBASEUNIFORMTYPE text,ZTEXT text,ZTITLE text,ZLIST integer,
          ZSORTORDER integer,ZREMOVEDFROMALL integer,ZCOPYCOUNT integer,ZAPP integer,ZAPPBUNDLEID text);
    """)
    examples = [
        ("public.text", "  keep whitespace\n", [("public.utf8-plain-text", b"  keep whitespace\n"),
                                               ("public.html", b"<b>keep whitespace</b>")]),
        ("public.image", "", [("public.png", PNG)]),
        ("public.url", "https://example.org/", [("public.utf8-plain-text", b"https://example.org/")]),
        ("public.file-url", "", [("public.file-url", b"file:///synthetic/missing-a"),
                                  ("public.file-url", b"file:///synthetic/missing-b")]),
        ("org.example.special", "", [("org.example.special", b"\x00\x01\xff")]),
    ]
    for number, (kind, text, parts) in enumerate(examples, 1):
        raw = plistlib.dumps([{"type": kind, "data": data} for kind, data in parts], fmt=plistlib.FMT_BINARY)
        # Exercise both Core Data inline and external storage.
        blob = b"\x01" + raw
        if number == 2:
            external = directory / ".PasteNow_SUPPORT/_EXTERNAL_DATA"
            external.mkdir(parents=True)
            (external / "synthetic-image").write_bytes(raw)
            blob = b"\x02synthetic-image\x00"
        connection.execute("insert into ZPASTEITEMDATA values(?,?,0)", (number, blob))
        connection.execute("insert into ZPASTEITEM values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (number, number, 700000000.123456 + number, 700000001.234567 + number,
                            kind, text, f"Synthetic {number}", 2 if number % 2 else 1,
                            number, 0, 1, 1, "org.example.synthetic"))
    connection.commit()
    connection.close()


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pastenow-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_path_escape_and_external_markers(self):
        self.assertEqual(m.blob_data(b"\x01hello", self.root, "db"), b"hello")
        with self.assertRaises(m.MigrationError):
            m.confined(self.root, "../escape")
        with self.assertRaises(m.MigrationError):
            m.blob_data(b"\x02../../escape", self.root, "db")
        with self.assertRaises(m.MigrationError):
            m.blob_data(b"\x03hello", self.root, "db")

    def test_export_preserves_formats_whitespace_time_and_repeated_types(self):
        source_fixture(self.root / "source")
        data = m.export_source(self.root / "source", self.root / "export")
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual(data["items"][0]["text"], "  keep whitespace\n")
        self.assertEqual(data["items"][0]["source_timestamp"], 700000001.123456)
        self.assertEqual([p["type"] for p in data["items"][3]["pasteboard_parts"]],
                         ["public.file-url", "public.file-url"])
        self.assertEqual(m.validate_export(self.root), data)
        path = self.root / "export" / data["items"][0]["pasteboard_parts"][0]["path"]
        path.write_bytes(b"tampered")
        with self.assertRaises(m.MigrationError):
            m.validate_export(self.root)

    def test_missing_attachment_and_invalid_timestamp_stop_export(self):
        source_fixture(self.root / "source")
        (self.root / "source/.PasteNow_SUPPORT/_EXTERNAL_DATA/synthetic-image").unlink()
        with self.assertRaises(FileNotFoundError):
            m.export_source(self.root / "source", self.root / "missing-export")
        connection = sqlite3.connect(self.root / "source/PasteNow.sqlite")
        connection.execute("update ZPASTEITEM set ZTIMESTAMP=null where Z_PK=1")
        connection.commit()
        connection.close()
        with self.assertRaises(m.MigrationError):
            m.export_source(self.root / "source", self.root / "bad-export")

    def test_sqlite_backup_includes_uncheckpointed_wal_rows(self):
        source = self.root / "source"
        source.mkdir()
        connection = sqlite3.connect(source / "test.sqlite")
        connection.execute("pragma journal_mode=wal")
        connection.execute("pragma wal_autocheckpoint=0")
        connection.execute("create table items(value)")
        connection.execute("insert into items values('committed-in-wal')")
        connection.commit()
        try:
            m.copy_store(source, self.root / "copy")
            snapshot = m.read_db(self.root / "copy/test.sqlite")
            self.assertEqual(snapshot.execute("select value from items").fetchone()[0], "committed-in-wal")
            snapshot.close()
        finally:
            connection.close()

    def test_reject_symlinks_workspace_overlap_and_changed_fingerprint(self):
        (self.root / "store").mkdir()
        file = self.root / "store/file"
        file.write_bytes(b"one")
        first = m.fingerprint(self.root / "store")
        file.write_bytes(b"two")
        self.assertNotEqual(first, m.fingerprint(self.root / "store"))
        (self.root / "store/link").symlink_to(file)
        with self.assertRaises(m.MigrationError):
            m.copy_store(self.root / "store", self.root / "copy")
        with self.assertRaises(m.MigrationError):
            m.check_workspace_location(self.root / "source/work", self.root / "source", self.root / "target")

    def test_running_app_is_rejected(self):
        with patch("migrate.subprocess.run", return_value=subprocess.CompletedProcess([], 0,
                   stdout="/Applications/Paste.app/Contents/MacOS/Paste\n")):
            with self.assertRaises(m.MigrationError):
                m.require_stopped()

    @unittest.skipUnless(sys.platform == "darwin", "Requires macOS compression library")
    def test_lzvn_grows_buffer_without_silently_truncating(self):
        raw = plistlib.dumps([{"type": "synthetic", "data": b"x" * (2 * 1024 * 1024)}], fmt=plistlib.FMT_BINARY)
        encode = ctypes.CDLL("/usr/lib/libcompression.dylib").compression_encode_buffer
        encode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
        encode.restype = ctypes.c_size_t
        buffer = ctypes.create_string_buffer(len(raw) * 2)
        length = encode(buffer, len(buffer), raw, len(raw), None, 0x801)
        self.assertGreater(length, 0)
        self.assertEqual(m.decode_payload(buffer.raw[:length]), plistlib.loads(raw))
        with self.assertRaises(m.MigrationError):
            m.decode_payload(buffer.raw[:length], max_bytes=1024 * 1024)


@unittest.skipUnless(sys.platform == "darwin" and (APP / m.MODEL).is_file(),
                     "Integration requires a locally installed Paste 7.0.2 model")
class NativeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory(prefix="pastenow-tools-")
        cls.fixture_binary = Path(cls.tools.name) / "create-fixture"
        subprocess.run(["xcrun", "swiftc", str(m.ROOT / "tests/CreatePasteFixture.swift"),
                        "-o", str(cls.fixture_binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tools.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pastenow-native-test-")
        self.root = Path(self.temp.name)
        source_fixture(self.root / "source")
        target = self.root / "target"
        target.mkdir()
        subprocess.run([str(self.fixture_binary), str(target), str(APP / m.MODEL)], check=True)
        connection = sqlite3.connect(target / "index.sqlite")
        connection.executescript("""
            create table items(id text primary key,app text,timestamp integer,type text,
                               title text,content text,device text);
            create table fts_queue(id text primary key);
            create table ocr_queue(id text primary key);
        """)
        connection.close()
        self.args = argparse.Namespace(source=self.root / "source", target=target,
                                       workspace=self.root / "workspace", paste_app=APP)

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_verify_replay_install_and_rollback(self):
        before = m.fingerprint(self.args.target)
        # Only synthetic stores are used. The real Paste app may remain open.
        with patch("migrate.require_stopped"):
            m.prepare(self.args)
        self.assertEqual(before, m.fingerprint(self.args.target))
        report = m.verify(self.args.workspace)
        self.assertEqual(report["records"], 5)
        self.assertEqual(report["representations"], 7)
        workspace = self.args.workspace
        subprocess.run([str(workspace / "import-paste"), str(workspace), str(APP)], check=True)
        self.assertEqual(m.verify(workspace), report)  # Stable IDs prevent duplicate records.
        manifest = json.loads((workspace / "manifest.json").read_text())
        manifest["prepared_fingerprint"] = m.fingerprint(workspace / "prepared/Paste")
        m.write_json(workspace / "manifest.json", manifest)
        # Changed current data must prevent an install.
        (self.args.target / "new-data").write_text("do not overwrite")
        with patch("migrate.require_stopped"), self.assertRaises(m.MigrationError):
            m.install(self.args)
        (self.args.target / "new-data").unlink()
        # An interrupted second rename must put the original directory back.
        real_rename = Path.rename

        def fail_ready(path, destination):
            if ".migration-ready-" in path.name:
                raise OSError("synthetic interrupted install")
            return real_rename(path, destination)

        with patch("migrate.require_stopped"), patch.object(Path, "rename", fail_ready), self.assertRaises(OSError):
            m.install(self.args)
        self.assertEqual(before, m.fingerprint(self.args.target))
        with patch("migrate.require_stopped"):
            m.install(self.args)
        installation = json.loads((workspace / "installation.json").read_text())
        self.assertEqual(before, m.fingerprint(Path(installation["rollback"])))
        connection = m.read_db(self.args.target / "db.sqlite")
        self.assertEqual(connection.execute("select count(*) from ZITEMENTITY").fetchone()[0], 6)
        connection.close()


if __name__ == "__main__":
    unittest.main()
