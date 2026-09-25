# CLAUDE.md

Three standalone Python 3 CLI scripts that back up, restore and administer every database on a CouchDB-compatible server (CouchDB 1.x–3.x, BigCouch, Cloudant). There is no package, no tests, no CI and no linter config. Each script runs on its own with its own argparse block.

| Script | Client library | Concurrency | What it does |
|---|---|---|---|
| `couchdb-backup.py` | `cloudant` (python-cloudant) | 8 threads | Dumps every DB from `_all_dbs` that passes `--match`/`--exclude` to `./dump.zip`; `--resume` |
| `couchdb-restore.py` | `cloudant` | 2 threads | For each DB in a dump: drop, recreate, bulk-load (`--dumpfile`, `--clean`) |
| `couchdb-tools.py` | `couchdb` (CouchDB-Python), `furl`, `humanize` | sequential | Per-DB size report; `--delete`, `--rebuild`, `--compact` |

## Safety

- `--host` defaults to `http://localhost:5984`, and on a dev machine that is usually a real server. Always pass `--host`, and run anything that writes against a server you created for the test.
- Restore deletes each DB before re-importing it, and there is no rollback. The delete swallows errors: if it fails, restore merges into the existing DB and logs 409s as `[ERR]` lines. If `bulk_docs` or an attachment upload fails after the delete, the DB stays empty or partial. `--clean` only deletes.
- Unfiltered backups include system DBs (`_users`, `_replicator`), and an unfiltered restore drops and recreates them. `--exclude='^_'` skips them.
- `del client[name]` does opposite things in the two libraries. In python-cloudant (backup, restore) it only evicts a local cache entry. In CouchDB-Python (tools) it sends HTTP DELETE and drops the database. Never move that idiom from one to the other.
- The scripts work in the current directory. Backup wipes `./dumps/` (unless `--resume` is given) and writes `./dump.zip`; restore wipes `./unpacked/`. Run them from a scratch directory, not the repo root.
- All three `print(args)` at startup, which echoes `--password`.

## Running and verifying

```bash
python3 -m venv .venv && . .venv/bin/activate   # Debian/Ubuntu need the python3.X-venv package
pip install -r requirements.txt                 # unpinned; resolves to cloudant 2.15.0 and CouchDB 1.2, both final releases
```

- There is no test suite. Offline checks: `python -m py_compile`, `pyflakes` and `--help`, none of which contact a server. Set `PYTHONDONTWRITEBYTECODE=1`, because `__pycache__/` in the repo is easy to miss (see Git).
- End to end: start a throwaway server on a free port, e.g. `sudo docker run -d --rm --name cbtest -p 127.0.0.1:5999:5984 -e COUCHDB_USER=admin -e COUCHDB_PASSWORD=admin couchdb:3.5`, then create `_users` and `_replicator`. Seed data shaped like the real workload: Kazoo (2600hz) clusters with thousands of DBs named `account/xx/yy/<hex>-YYYYMM`, monthly DBs of 100k+ docs, and voicemail attachments shared across DBs.
- To check a backup change, compare the old (`git show HEAD:couchdb-backup.py`) and new dumps with `PYTHONHASHSEED=0`; without it, key order inside design docs changes from run to run. Then restore into a second empty server and compare docs (ignoring `_rev`) and attachment bytes.
- CouchDB 3.x has no admin party and `_all_dbs` is admin-only, so pass both `--user` and `--password`. With only one of them, backup and restore silently switch to admin party and send no credentials; credentials embedded in `--host` are ignored too.
- Exit codes: backup exits 1 if zip is missing or fails, or if any DB failed. Restore and tools exit 0 even when DBs fail, so read their output (`[ERR]` lines, `generated an exception`, tracebacks on stderr).

## Dump format: the contract between backup and restore

```
dump.zip                        zip of dumps/, entries at the archive root
  <sha1(db_name)>.json          line 1: DB name; then one JSON doc per line (_all_docs include_docs, design docs included)
  attachments/<sha1(digest)>    attachment bytes; named by sha1 of the stub's "digest" string ("md5-...")
```

- Restore relies on these facts: line 1 is the name, every other line is a doc, each `_attachments` stub has `digest` and `content_type`, and the bytes sit at `attachments/sha1(digest)`. It globs `*.json` and never recomputes the file name. The dump has no version marker, so change both scripts together and keep existing `dump.zip` files restorable.
- Attachments are content-addressed across DBs, so identical content is stored once.
- Backups are shallow: only the winning revision of each live doc. No history, conflicts, deleted docs, `_local` docs or `_security`, and restored DBs are always non-partitioned. Design docs also gain empty `options`/`views`/`indexes`/`lists`/`shows` keys from python-cloudant's `DesignDocument`.
- Restore strips `_rev` and `_attachments`, sends the whole DB in one `bulk_docs` call, then uploads attachments one at a time. `put_attachment` doesn't URL-encode names, so names containing `?` or `#` come back truncated.

## Backup internals

- **Memory must not grow with DB size.** Eight threads each dump a DB at once, and real DBs are multi-GB. python-cloudant's `Database.__iter__` caches every document it yields in the `Database` dict, so backup writes each doc straight to the file and `db.pop`s it. Buffering a whole DB is what got the old version OOM-killed, so don't reintroduce it. Attachments are still buffered whole by `get_attachment`, roughly 2× the attachment size per thread.
- **Completion markers.** A DB is written to `<sha1>.json.partial` and renamed to `.json` when finished. An attachment goes to `<file>.<thread id>.partial` and is renamed into place, because threads can download the same attachment at once.
- **What `--resume` trusts.** It skips a `.json` only if it has at least one doc line after the name line and ends in `\n`; empty DBs are simply dumped again. This catches the empty, name-only and cut-off files that older versions left when a DB was killed or failed. It can't catch an old file cut off exactly at a line boundary, which is why the README says to do a full run once after upgrading. An untrusted `.json` is deleted before the retry, so a DB that fails again is left out of `dump.zip` instead of being zipped half done. An attachment is trusted only if its size equals the stub's `length`; attachments uploaded with `Content-Encoding: gzip` never match, so they are just downloaded again.
- **zip.** The system `zip` (checked at startup) builds `<realpath of dump.zip>.tmp/dump.zip` from inside `dumps/`, and the result then replaces `dump.zip`. The realpath means a `dump.zip` symlink is updated rather than replaced, and zip's own temp file stays in that `.tmp/` dir, which is cleaned at startup. Don't zip into an existing archive: `zip` merges into it instead of replacing it. The old `dump.zip` survives a failed zip, so the disk needs room for both.

## Restore and tools

- Restore runs 2 workers (cut from 8 in "Reduce memory leaks") and sleeps 10 ms per DB ("less aggressive restore"). The README Troubleshooting section ties 500s during big restores to server limits (`max_dbs_open`, `LimitNOFILE`), so test against many DBs before raising concurrency.
- Restore still reads each DB file whole (`readlines()`) and sends one `bulk_docs`, so its memory scales with the largest DB.
- Restore parses all JSON before it deletes the DB. Keep that order: a corrupt dump then fails before anything is dropped.
- `couchdb-tools.py` is broken on CouchDB ≥ 3.0. `process_database` reads `info()['data_size']`, which 3.0 removed, before any action runs, so each DB just prints `'data_size'` and `--delete`/`--rebuild`/`--compact` never run. Fixing that re-enables the destructive flags for anyone already running them, so raise it with the user rather than fixing it on the side. `--clean` and `--purge` are accepted but not implemented, and `--delete --rebuild` leaves the DB deleted.

## Code conventions

- Each script has a `### Functions` section and an `if __name__ == "__main__":` block. The block sets module globals that the workers read: `args`, `client`, `re_match`/`re_exclude` (only when their flag is given) and the `path_*` variables. The hyphenated file names can't be imported; for offline tests, load them with `importlib.util.spec_from_file_location`, set the globals and call `process_database`.
- Workers return their log lines as one string, and the main thread prints it, so output from different threads doesn't interleave.
- `--match`/`--exclude` use `re.match`, which anchors only at the start: `--match=users` matches `users_old` but not `_users`. `--exclude` is applied after `--match`.
- Shared flags are copy-pasted into each script's argparse block, and some same-named flags mean different things. Restore `--clean` drops DBs; tools `--clean` is an unimplemented "delete all docs"; tools `--delete` is closer to restore `--clean`.
- The README promises Python ≥ 3.5, so don't use f-strings; use `.format()`, `%` or `+`. Follow the surrounding code for the `### <name>` end markers and for print spacing, which is mixed.
- Both client libraries are dead upstream: `cloudant` is deprecated, and its successor `ibmcloudant` has a different API; `CouchDB` stopped at 1.2. The scripts depend on their dict-cache semantics, so don't swap or upgrade them as a side task.
- Backup and restore build the `Cloudant` client the same way: `admin_party` when credentials are missing, and `use_basic_auth` for servers with `require_valid_user = true`. Keep the two in sync.

## Git

- `.gitignore` is `/*` with exceptions only for `.gitignore` and `README.md`; `!.py` matches a file literally named `.py`. Tracked files stay tracked, but any new file, this one included, needs `git add -f` or its own exception. Stray `__pycache__/`, `dumps/` or `dump.zip` in the repo won't show up in `git status`, so check with `git status --ignored`. Several files have no trailing newline, so `echo ... >> .gitignore` glues lines together.
- Commit subjects are short plain English, usually capitalized, with no prefix or body (e.g. "Reduce memory leaks", "Improve exception logging and skip empty DBs").
