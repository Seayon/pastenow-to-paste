# Clipaste migration review

Reviewed 2026-10-20, repository commit [`e966dd5a357bf49021487c517bcbeafba4236fb2`](https://github.com/gangz1o/Clipaste/tree/e966dd5a357bf49021487c517bcbeafba4236fb2). These observations concern the migration paths at this revision, not every Clipaste feature.

| Area | Observed implementation | Decision here |
| --- | --- | --- |
| PasteNow input | JSON file, recursively extracting text, timestamps, app names and list names | Read database plus attachments to retain binary representations |
| Text fidelity | Selects nonempty strings after trimming surrounding whitespace | Retain exact original text and bytes |
| Images and rich text | The PasteNow migration row contains text and metadata; this route does not carry raw image/HTML/RTF payloads | Preserve every stored format and independently compare SHA-256 |
| Destination | Clipaste's own SwiftData records | Installed Paste model through Core Data on a copy |
| Duplicate policy | Text content hashes; may update existing timestamps/group membership | Source identity + row IDs; distinct source records remain distinct |
| Time handling | JSON path considers plausible epoch/unit interpretations; SQLite Core Data path explicitly uses Apple's reference date | Known database schema: preserve original reference-date values without guessing |
| Database reads | Paste SQLite path copies the main file, then opens the copy read-only/immutable | SQLite backup API includes committed WAL changes, plus stopped-app attachment snapshots |
| Code structure | Separate loaders, normalized rows, persistence, errors and reports | Keep extraction, native conversion and independent verification separate |

Source files:

- [PasteNow JSON loader](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager%2BPasteNow.swift)
- [Routing and persistence](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager.swift)
- [Migration row model](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager%2BModels.swift)
- [Paste SQLite loader](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager%2BPaste.swift)
- [SQLite helper](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager%2BSQLite.swift)
- [Timestamp decoding](https://github.com/gangz1o/Clipaste/blob/e966dd5a357bf49021487c517bcbeafba4236fb2/clipaste/Services/MigrationManager%2BTimestamps.swift)

The SQLite helper's WAL-safe comment is not proof that a main-file-only copy includes uncheckpointed WAL rows. Our synthetic test holds committed data in WAL and verifies that the SQLite backup API includes it.

GitHub repository and code searches for PasteNow/Paste migration, converter/export terms and the relevant database table names did not locate a direct PasteNow → Paste full-fidelity converter. This is a bounded search result, not proof that none exists.

Clipaste is GPL-3.0. It was reviewed for behavioral comparison only; no source code or assets were copied into this independently implemented MIT project.
