#!/usr/bin/env python3
"""Local, staged PasteNow 2.32 -> Paste 7.0.2 migration. Standard library only."""

from __future__ import annotations

import argparse
import collections
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
SOURCE = HOME / "Library/Group Containers/KAQ3M6FSM7.app.pastenow.PasteNow/Documents"
TARGET = HOME / "Library/Containers/com.wiheads.paste/Data/Library/Application Support/Paste"
MODEL = "Contents/Resources/PastePackages_PasteCore.bundle/Contents/Resources/v5Paste.momd/v11.mom"
PREFIX = "pastenow-"


class MigrationError(Exception):
    pass


def read_db(path):
    connection = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fingerprint(root):
    """Include WAL files, attachments and metadata; SHM is a transient read cache."""
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise MigrationError("Store contains a symlink; refusing to follow it")
        if path.is_file() and not path.name.endswith("-shm"):
            if path.name.endswith("-wal") and path.stat().st_size == 0:
                continue
            result[str(path.relative_to(root))] = digest(path)
    return result


def confined(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise MigrationError("Data file escapes its workspace")
    return path


def blob_data(blob, store, database):
    if not blob:
        raise MigrationError("Missing binary data")
    if blob[0] == 1:
        return blob[1:]
    if blob[0] == 2:
        name = blob[1:].rstrip(b"\0").decode("utf-8")
        if Path(name).name != name:
            raise MigrationError("Invalid external-data filename")
        return confined(Path(store) / f".{database}_SUPPORT/_EXTERNAL_DATA", name).read_bytes()
    raise MigrationError("Unsupported binary-data marker")


def decode_payload(raw, compressed=True, max_bytes=512 * 1024 * 1024):
    if not compressed:
        return plistlib.loads(raw)
    function = ctypes.CDLL("/usr/lib/libcompression.dylib").compression_decode_buffer
    function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_size_t
    size = 1024 * 1024
    while size <= max_bytes:
        output = ctypes.create_string_buffer(size)
        written = function(output, size, raw, len(raw), None, 0x801)  # COMPRESSION_LZVN
        # A full buffer can mean truncated output. Retry instead of parsing it.
        if 0 < written < size:
            return plistlib.loads(output.raw[:written])
        size *= 2
    raise MigrationError("Cannot decompress payload within the 512 MiB limit")


def require_stopped(paste_app=Path("/Applications/Paste.app")):
    binaries = [str(Path(paste_app) / "Contents/MacOS/Paste")]
    result = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=True)
    for command in result.stdout.splitlines():
        command = command.strip()
        if command in binaries or command.endswith(("/Paste.app/Contents/MacOS/Paste",
                                                   "/PasteNow.app/Contents/MacOS/PasteNow")):
            raise MigrationError("Quit Paste and PasteNow before preparing or installing")


def copy_store(source, destination):
    """Caller must keep the owning app stopped throughout the copy."""
    before = fingerprint(source)  # Reject symlinks before copytree can dereference them.
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("*-wal", "*-shm"))
    for path in Path(source).glob("*.sqlite"):
        old = read_db(path)
        new = sqlite3.connect(Path(destination) / path.name)
        try:
            old.backup(new)
        finally:
            new.close()
            old.close()
    if fingerprint(source) != before:
        raise MigrationError("Store changed while taking its snapshot; quit its owning app and retry")


def require_schema(connection, requirements):
    for table, columns in requirements.items():
        available = {r[1] for r in connection.execute(f'pragma table_info("{table}")')}
        if not set(columns) <= available:
            raise MigrationError(f"Unsupported database schema: {table}")


def export_source(source, output):
    """Export every representation without printing clipboard contents."""
    output = Path(output)
    assets = output / "assets"
    assets.mkdir(parents=True, exist_ok=False)
    connection = read_db(Path(source) / "PasteNow.sqlite")
    try:
        require_schema(connection, {
            "Z_METADATA": ["Z_UUID"],
            "ZPASTEITEM": ["Z_PK", "ZITEMDATA", "ZTIMESTAMP", "ZUPDATEDATE", "ZBASEUNIFORMTYPE",
                           "ZTEXT", "ZTITLE", "ZLIST", "ZSORTORDER", "ZREMOVEDFROMALL", "ZCOPYCOUNT",
                           "ZAPP", "ZAPPBUNDLEID"],
            "ZPASTEITEMDATA": ["Z_PK", "ZDATA", "ZCOMPRESSED"],
            "ZAPPLICATION": ["Z_PK", "ZBUNDLEID", "ZNAME", "ZICON"],
            "ZPASTELIST": ["Z_PK", "ZNAME", "ZLISTTYPE", "ZSORTWEIGHT"],
        })
        if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Source database integrity check failed")
        source_uuid = connection.execute("select Z_UUID from Z_METADATA").fetchone()[0]
        if not isinstance(source_uuid, str) or not source_uuid:
            raise MigrationError("Source database has no stable identity")
        namespace = hashlib.sha256(source_uuid.encode()).hexdigest()[:24]
        apps = {}
        for row in connection.execute("select * from ZAPPLICATION"):
            app = {"id": row["Z_PK"], "bundle_id": row["ZBUNDLEID"], "name": row["ZNAME"]}
            if row["ZICON"]:
                icon = blob_data(row["ZICON"], source, "PasteNow")
                path = assets / (hashlib.sha256(icon).hexdigest() + ".bin")
                path.write_bytes(icon)
                app["icon_path"] = str(path.relative_to(output))
            apps[row["Z_PK"]] = app
        lists = {str(r["Z_PK"]): {"name": r["ZNAME"], "type": r["ZLISTTYPE"],
                 "sort_order": r["ZSORTWEIGHT"]} for r in connection.execute("select * from ZPASTELIST")}
        items = []
        for row in connection.execute("select i.*,d.ZDATA as PAYLOAD,d.ZCOMPRESSED as COMPRESSED "
                                      "from ZPASTEITEM i left join ZPASTEITEMDATA d "
                                      "on i.ZITEMDATA=d.Z_PK order by i.ZTIMESTAMP,i.Z_PK"):
            sid = row["Z_PK"]
            if not isinstance(row["ZTIMESTAMP"], (int, float)) or not math.isfinite(row["ZTIMESTAMP"]):
                raise MigrationError(f"Invalid timestamp for source row {sid}")
            item = {"source_id": sid, "identifier": f"{PREFIX}{namespace}-{sid}",
                    "type": row["ZBASEUNIFORMTYPE"], "source_timestamp": row["ZTIMESTAMP"],
                    "source_updated_at": row["ZUPDATEDATE"], "text": row["ZTEXT"],
                    "title": row["ZTITLE"], "list_id": row["ZLIST"],
                    "sort_order": row["ZSORTORDER"], "removed_from_all": row["ZREMOVEDFROMALL"],
                    "copy_count": row["ZCOPYCOUNT"], "source_app_id": row["ZAPP"],
                    "source_bundle_id": row["ZAPPBUNDLEID"], "pasteboard_parts": []}
            raw = blob_data(row["PAYLOAD"], source, "PasteNow")
            parts = decode_payload(raw, bool(row["COMPRESSED"]))
            if not isinstance(parts, list) or not parts:
                raise MigrationError(f"Invalid or empty payload for source row {sid}")
            for part in parts:
                data, kind = part["data"], part["type"]
                if not isinstance(data, bytes) or not isinstance(kind, str):
                    raise MigrationError(f"Invalid representation for source row {sid}")
                sha = hashlib.sha256(data).hexdigest()
                path = assets / (sha + ".bin")
                if not path.exists():
                    path.write_bytes(data)
                item["pasteboard_parts"].append({"type": kind, "path": str(path.relative_to(output)),
                                                "bytes": len(data), "sha256": sha})
            items.append(item)
        result = {"format_version": 1, "namespace": namespace, "items": items,
                  "lists": lists, "applications": list(apps.values())}
        write_json(output / "records.json", result)
        return result
    finally:
        connection.close()


def validate_export(workspace):
    export = Path(workspace) / "export"
    data = json.loads((export / "records.json").read_text())
    if data["format_version"] != 1:
        raise MigrationError("Unknown export format")
    identifiers = [item["identifier"] for item in data["items"]]
    if len(identifiers) != len(set(identifiers)):
        raise MigrationError("Duplicate exported identifiers")
    checked = set()
    for item in data["items"]:
        for part in item["pasteboard_parts"]:
            path = confined(export, part["path"])
            key = (path, part["sha256"], part["bytes"])
            if key not in checked:
                if path.stat().st_size != part["bytes"] or digest(path) != part["sha256"]:
                    raise MigrationError("Exported representation failed SHA-256 verification")
                checked.add(key)
    return data


def decode_preview(blob, store):
    raw = blob_data(blob, store, "db")
    return plistlib.loads(raw) if raw.startswith(b"bplist") else json.loads(raw)


def build_index(store, data):
    main = read_db(Path(store) / "db.sqlite")
    index = sqlite3.connect(Path(store) / "index.sqlite")
    try:
        for item in data["items"]:
            row = main.execute("select i.ZRAWTYPE,i.ZRAWPREVIEW,a.ZNAME,dev.ZNAME as DEVICE "
                               "from ZITEMENTITY i left join ZAPPLICATIONENTITY a on "
                               "i.ZSOURCEAPPLICATION=a.Z_PK left join ZDEVICEENTITY dev on "
                               "i.ZDEVICE=dev.Z_PK where i.ZIDENTIFIER=?", (item["identifier"],)).fetchone()
            kind = {1: "Image", 3: "File", 4: "Link", 5: "Text"}[row["ZRAWTYPE"]]
            content = item["text"] or ""
            if kind == "File":
                content = "\n".join(decode_preview(row["ZRAWPREVIEW"], store)["filePaths"])
            index.execute("insert into items(id,app,timestamp,type,title,content,device) "
                          "values(?,?,?,?,?,?,?) on conflict(id) do update set app=excluded.app,"
                          "timestamp=excluded.timestamp,type=excluded.type,title=excluded.title,"
                          "content=excluded.content,device=excluded.device",
                          (item["identifier"], row["ZNAME"] or "", int(item["source_timestamp"] + 978307200),
                           kind, item["title"] or "", content, row["DEVICE"] or "Mac"))
            index.execute("insert or ignore into fts_queue(id) values(?)", (item["identifier"],))
            if kind == "Image":
                index.execute("insert or ignore into ocr_queue(id) values(?)", (item["identifier"],))
        index.commit()
    finally:
        index.close()
        main.close()


def verify(workspace, store=None):
    workspace = Path(workspace)
    data = validate_export(workspace)
    store = Path(store) if store is not None else workspace / "prepared/Paste"
    main = read_db(store / "db.sqlite")
    original = read_db(workspace / "backup/Paste/db.sqlite")
    index = read_db(store / "index.sqlite")
    representations = 0
    try:
        for connection in (main, original, index):
            if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise MigrationError("Database integrity check failed")
        apps = {a["id"]: a for a in data["applications"]}
        for item in data["items"]:
            row = main.execute("select i.*,d.ZRAWPASTEBOARDITEMS,a.ZBUNDLEIDENTIFIER,"
                               "l.ZIDENTIFIER as LISTID,l.ZNAME as LISTNAME "
                               "from ZITEMENTITY i join ZITEMDATAENTITY d on i.ZDATA=d.Z_PK "
                               "left join ZAPPLICATIONENTITY a on i.ZSOURCEAPPLICATION=a.Z_PK "
                               "left join ZLISTENTITY l on i.ZLIST=l.Z_PK where i.ZIDENTIFIER=?",
                               (item["identifier"],)).fetchone()
            if row is None:
                raise MigrationError("Missing migrated record")
            source_list = data["lists"].get(str(item["list_id"]), {})
            expected_list = (f"pastenow-list-{data['namespace']}-{item['list_id']}"
                             if source_list.get("type") == 0 else "sharedPasteboardHistory")
            bundle = (apps.get(item["source_app_id"], {}).get("bundle_id")
                      or item["source_bundle_id"] or None)
            if (row["ZTIMESTAMP"] != item["source_timestamp"]
                    or row["ZCREATEDAT"] != item["source_timestamp"]
                    or row["ZTITLE"] != (item["title"] or None)
                    or row["ZBUNDLEIDENTIFIER"] != bundle or row["LISTID"] != expected_list):
                raise MigrationError("Migrated metadata differs from source")
            if source_list.get("type") == 0 and row["LISTNAME"] != source_list["name"]:
                raise MigrationError("Pinboard name differs from source")
            if item["source_updated_at"] is not None and row["ZUPDATEDAT"] != item["source_updated_at"]:
                raise MigrationError("Update timestamp differs from source")
            if source_list.get("type") == 0 and row["ZDISPLAYORDERINPINBOARD"] != (item["sort_order"] or 0):
                raise MigrationError("Pinboard record order differs from source")
            raw = blob_data(row["ZRAWPASTEBOARDITEMS"], store, "db")
            actual = [(kind, len(group["dataByType"][kind]),
                       hashlib.sha256(group["dataByType"][kind]).hexdigest())
                      for group in plistlib.loads(raw) for kind in group["types"]]
            expected = [(p["type"], p["bytes"], p["sha256"]) for p in item["pasteboard_parts"]]
            if actual != expected:
                raise MigrationError("Migrated clipboard payload differs from source")
            representations += len(actual)
            cached = index.execute("select timestamp,title from items where id=?", (item["identifier"],)).fetchone()
            if cached is None or tuple(cached) != (int(item["source_timestamp"] + 978307200), item["title"] or ""):
                raise MigrationError("Missing or inconsistent search index record")
        fields = "i.ZIDENTIFIER,i.ZTIMESTAMP,i.ZTITLE,i.ZCHECKSUM,i.ZRAWTYPE"
        query = f"select {fields},i.ZRAWPREVIEW,d.ZRAWPASTEBOARDITEMS from ZITEMENTITY i " \
                "left join ZITEMDATAENTITY d on i.ZDATA=d.Z_PK"
        for row in original.execute(query):
            after = main.execute(query + " where i.ZIDENTIFIER=?", (row["ZIDENTIFIER"],)).fetchone()
            if after is None or tuple(after)[:5] != tuple(row)[:5]:
                raise MigrationError("An existing Paste record was changed or removed")
            for column in ("ZRAWPREVIEW", "ZRAWPASTEBOARDITEMS"):
                if row[column] is None:
                    if after[column] is not None:
                        raise MigrationError("An existing Paste record's empty content was changed")
                    continue
                before_data = blob_data(row[column], workspace / "backup/Paste", "db")
                if blob_data(after[column], store, "db") != before_data:
                    raise MigrationError("An existing Paste record's content was changed")
        clipboard = main.execute("select ZRAWATTRIBUTES from ZLISTENTITY where "
                                 "ZIDENTIFIER='sharedPasteboardHistory'").fetchone()
        if json.loads(clipboard[0]).get("capacity") != 0:
            raise MigrationError("Clipboard retention is not unlimited")
        return {"records": len(data["items"]), "representations": representations,
                "types": dict(collections.Counter(i["type"] for i in data["items"])),
                "content_mismatches": 0, "maximum_timestamp_difference_seconds": 0,
                "existing_paste_records_preserved": True, "integrity": "ok"}
    finally:
        main.close()
        original.close()
        index.close()


def check_workspace_location(workspace, source, target):
    workspace = Path(workspace).resolve()
    repo = ROOT.resolve()
    for other in (source, target, repo):
        other = Path(other).resolve()
        if workspace.is_relative_to(other) or other.is_relative_to(workspace):
            raise MigrationError("Workspace must be outside the repository and both application stores")


def prepare(args):
    require_stopped(args.paste_app)
    source, target = args.source.resolve(), args.target.resolve()
    workspace = args.workspace.expanduser().resolve()
    check_workspace_location(workspace, source, target)
    info = plistlib.loads((args.paste_app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleShortVersionString") != "7.0.2":
        raise MigrationError("Only Paste 7.0.2 has been validated; refusing another version")
    if not (args.paste_app / MODEL).is_file():
        raise MigrationError("Validated Paste model v11 is missing")
    workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
    (workspace / "backup").mkdir()
    print("Creating private source and target snapshots...", flush=True)
    copy_store(source, workspace / "backup/PasteNow")
    original_fingerprint = fingerprint(target)
    copy_store(target, workspace / "backup/Paste")
    require_stopped(args.paste_app)
    if fingerprint(target) != original_fingerprint:
        raise MigrationError("Paste changed while creating its snapshot")
    print("Exporting original representations...", flush=True)
    data = export_source(workspace / "backup/PasteNow", workspace / "export")
    (workspace / "prepared").mkdir()
    copy_store(workspace / "backup/Paste", workspace / "prepared/Paste")
    binary = workspace / "import-paste"
    subprocess.run(["xcrun", "swiftc", str(ROOT / "ImportPaste.swift"), "-o", str(binary)], check=True)
    subprocess.run([str(binary), str(workspace), str(args.paste_app.resolve())], check=True)
    build_index(workspace / "prepared/Paste", data)
    report = verify(workspace)
    write_json(workspace / "verification.json", report)
    if fingerprint(target) != original_fingerprint:
        raise MigrationError("Paste data changed during preparation; do not install this workspace")
    write_json(workspace / "manifest.json", {"format_version": 1, "target": str(target),
               "paste_app": str(args.paste_app.resolve()), "target_fingerprint": original_fingerprint,
               "prepared_fingerprint": fingerprint(workspace / "prepared/Paste")})
    print(json.dumps(report))
    print("Prepared and verified. Application stores were not replaced. Use install explicitly when ready.")


def install(args):
    workspace = args.workspace.expanduser().resolve()
    manifest = json.loads((workspace / "manifest.json").read_text())
    if manifest["format_version"] != 1:
        raise MigrationError("Unknown workspace format")
    app = Path(manifest["paste_app"])
    require_stopped(app)
    target = Path(manifest["target"]).resolve()
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise MigrationError("Workspace overlaps the target store")
    if fingerprint(target) != manifest["target_fingerprint"]:
        raise MigrationError("Paste changed since prepare. Create a fresh workspace before installing")
    verify(workspace)
    prepared = workspace / "prepared/Paste"
    if fingerprint(prepared) != manifest["prepared_fingerprint"]:
        raise MigrationError("Prepared files changed since verification")
    token = uuid.uuid4().hex[:12]
    ready = target.with_name(target.name + ".migration-ready-" + token)
    rollback = target.with_name(target.name + ".migration-backup-" + token)
    # Copy first. The current store is kept on the same volume for a rename rollback.
    copy_store(prepared, ready)
    verify(workspace, store=ready)
    require_stopped(app)
    if fingerprint(target) != manifest["target_fingerprint"]:
        shutil.rmtree(ready)
        raise MigrationError("Paste changed while installing; current store was not replaced")
    journal = {"target": str(target), "rollback": str(rollback), "ready": str(ready), "status": "installing"}
    write_json(workspace / "installation.json", journal)
    target.rename(rollback)
    try:
        ready.rename(target)
    except BaseException:
        rollback.rename(target)
        raise
    journal["status"] = "installed"
    write_json(workspace / "installation.json", journal)
    print("Installed. Original store kept at:", rollback)
    print("Open Paste, check search/copy and other devices before deleting either backup.")


def inspect(args):
    result = {}
    for name, path, table in (("PasteNow", args.source / "PasteNow.sqlite", "ZPASTEITEM"),
                              ("Paste", args.target / "db.sqlite", "ZITEMENTITY")):
        connection = read_db(path.resolve())
        try:
            result[name] = {"records": connection.execute(f"select count(*) from {table}").fetchone()[0],
                            "integrity": connection.execute("pragma quick_check").fetchone()[0]}
        finally:
            connection.close()
    print(json.dumps(result))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, default=SOURCE, help="PasteNow Documents directory")
        command.add_argument("--target", type=Path, default=TARGET, help="Paste data directory")
        if name == "prepare":
            command.add_argument("--workspace", type=Path, required=True, help="New private directory outside this repository")
            command.add_argument("--paste-app", type=Path, default=Path("/Applications/Paste.app"))
    for name in ("verify", "install"):
        commands.add_parser(name).add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify(args.workspace.expanduser().resolve())))
        else:
            globals()[args.command](args)
    except (MigrationError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
