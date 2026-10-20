# PasteNow → Paste

[中文说明](README.zh-CN.md)

Move clipboard history from **PasteNow 2.32 to Paste 7.0.2 on macOS**, preserving original copy timestamps and raw clipboard representations, including images, HTML and rich text.

An independent migration utility, not an official import interface. Python standard library + Apple's Swift/Core Data frameworks. No network calls, third-party runtime dependencies, bundled vendor models, or changes to either application binary.

## Compatibility

| Component | Validated scope |
| --- | --- |
| PasteNow | 2.32 local Core Data database and external attachments |
| Paste | 7.0.2, bundled `v5Paste.momd/v11.mom` model |
| Runtime | macOS, Python 3.10+, Xcode Command Line Tools (`xcrun swiftc`) |
| Cross-device sync | Not validated; local verification does not prove iCloud convergence |

The writer refuses other Paste versions or an incompatible model. Source table/column checks reject unsupported schemas; this is not a promise of compatibility with all PasteNow releases. Open Paste once before migrating so it has a device record, clipboard history and search database.

## Usage

1. Set up Python 3.10+ and Apple's Command Line Tools. Check `python3 --version` and `xcrun swiftc --version`.
2. Quit both PasteNow and Paste. Keep them closed until installation completes. The tool checks running processes; it never quits apps for you.
3. Use a **new workspace outside this repository**, with enough free space for the source snapshot, original Paste store, exported payloads, converted store and installation copy. Several times the combined library size may be needed.

```sh
git clone https://github.com/Seayon/pastenow-to-paste.git
cd pastenow-to-paste

# Read-only aggregate inspection: no clipboard text is printed.
python3 migrate.py inspect

# Creates private snapshots and a converted copy. Does not replace either app's store.
python3 migrate.py prepare --workspace "$HOME/PasteMigration/work-01"

# Independently checks raw payload hashes, metadata, original records and index coverage.
python3 migrate.py verify --workspace "$HOME/PasteMigration/work-01"

# Explicitly installs the converted copy, preserving the current store beside it.
python3 migrate.py install --workspace "$HOME/PasteMigration/work-01"
```

Custom locations: `prepare` and `inspect` accept `--source` (the PasteNow **Documents directory**) and `--target` (the Paste **data directory**). `prepare` also accepts `--paste-app`. See `python3 migrate.py prepare --help`.

The tool sets the converted Paste history retention to **unlimited** so old records are not removed on launch. This change is in the prepared store and becomes active only after installation. Installation is refused if the original Paste store has changed since preparation: create a fresh workspace rather than overwriting new history.

After installation, open Paste and test search, image copy and rich-text paste. Check other devices separately if using iCloud. Keep backups until satisfied.

## What is preserved

- Every stored clipboard representation, byte for byte, including PNG/TIFF, HTML/RTF and app-specific data. SHA-256 checks compare source exports against the converted store.
- Original copy time and available update time, using the exact Core Data reference-date values.
- Titles, source application bundle identifiers/names and available icons, manual lists and list item order.
- Existing Paste records and their raw payloads/previews.
- Search cache entries, plus native full-text/OCR work queues. Paste performs queued indexing/OCR when it runs.

Duplicate text is not merged: distinct source records can have different dates, formats and lists. Stable IDs derived from the source database identity and row ID prevent replay duplicates within this tool. They do **not** deduplicate imports performed by other tools or an earlier one-off migration.

Boundaries:

- File clips preserve stored representations and paths. Referenced original files are not copied; deleted files cannot be reconstructed from a path.
- Smart-list rules, source copy counters and source-specific hidden-state semantics have no direct mapping. Counters/hidden flags remain in the private export; all source database rows are migrated. Manual lists are preserved, not smart filters.
- Unrenderable app-specific clips receive a descriptive preview while retaining their original bytes.
- Unsupported schemas, missing attachments, invalid timestamps and unreadable image payloads stop preparation instead of silently skipping records.

## How it works

```text
PasteNow database + attachments (read-only)
    → stopped-app snapshot using SQLite backup API, including committed WAL data
    → LZVN decompression and binary-plist decoding
    → private JSON manifest + content-addressed raw files
    → Swift loads the model from your installed Paste application
    → Core Data creates records/relationships on a Paste store copy
    → search cache + queues → independent verification
    → explicit installation with a retained rollback directory
```

No model or application executable is redistributed. The program uses public Apple framework APIs to manipulate a copy of your local data. The storage format itself is undocumented and version-specific.

## Rollback and recovery

`install` prints the sibling directory containing the pre-install store, named `Paste.migration-backup-<token>`. Its exact path is also recorded in the private workspace's `installation.json`. The workspace keeps additional source and target snapshots.

To restore locally: quit both apps, move the current `Paste` data directory aside, then rename the recorded rollback directory back to `Paste`. Keep all directories until the restored app has been checked. A local rollback does not undo already propagated iCloud changes.

Installation uses two same-volume renames. If the second fails, the tool attempts to rename the original back immediately. If the computer loses power between renames, consult `installation.json`: the original is at `rollback`, and the converted copy is at `ready`. Do not delete either while recovering.

## Privacy

Workspaces contain your complete clipboard history, which may include passwords, tokens and personal files. They are created outside the source repository with owner-only permissions. Do not upload them, attach them to issues, or run this against real histories in public CI. There is no telemetry. Normal output contains counts and status, not clipboard content.

## Tests and evidence

```sh
python3 -m unittest discover -s tests -v
```

Tests cover inline/external data, whitespace, repeated formats, binary payloads, fractional timestamps, multi-buffer LZVN decompression, committed WAL rows, missing files, tampering, symlinks, changed-store refusal, replay and installation rollback.

The optional native integration test creates **artificial databases** using your locally installed Paste model, then exercises prepare → verify → replay → install → rollback preservation. It never uses your application stores. Public CI skips this test because Paste's proprietary model is not distributed; macOS CI still compiles both Swift programs and runs the compression tests.

The original one-off implementation behind this project migrated a local 1,825-record library and checked 5,786 representations, with no content differences and zero copy-time drift; search and text/image copy were manually tested. The generalized CLI is a subsequent implementation verified with synthetic integration tests. These are distinct validation scopes, not a claim that every version/device has been tested.

## Related work

We reviewed [Clipaste's migration implementation](docs/clipaste-review.md). Its PasteNow route imports text from JSON into Clipaste, not full-fidelity history into Paste. This project uses its independently developed database/attachment conversion approach. No Clipaste code is included.

MIT license. PasteNow, Paste and Clipaste names belong to their respective authors; this project is unaffiliated.
