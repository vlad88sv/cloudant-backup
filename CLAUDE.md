# CLAUDE.md

Three standalone Python 3 CLI scripts that back up, restore and administer every database on a CouchDB-compatible server (CouchDB 1.x–3.x, BigCouch, Cloudant). There is no package, no tests, no CI and no linter config. Each script runs on its own with its own argparse block.

| Script | Client library | Concurrency | What it does |
|---|---|---|---|
| `couchdb-backup.py` | `cloudant` (python-cloudant) | 8 threads | Dumps every DB from `_all_dbs` that passes `--match`/`--exclude` to `./dump.zip`; `--resume` |
| `couchdb-restore.py` | `cloudant` | 2 threads | For each DB in a dump: drop, recreate, import in batches (`--dumpfile`, `--clean`, `--include-system-dbs`) |
| `couchdb-tools.py` | `couchdb` (CouchDB-Python), `furl`, `humanize` | sequential | Per-DB size report; `--delete`, `--rebuild`, `--compact`, `--include-system-dbs` |

## Safety

- `--host` defaults to `http://localhost:5984`, and on a dev machine that is usually a real server. Always pass `--host`, and run anything that writes against a server you created for the test.
- Restore deletes each DB before re-importing it, and there is no rollback. Only "doesn't exist" is ignored on delete; any other delete error fails that DB rather than merging into the old one. A failure after the drop (server down, 5xx) leaves the DB partial. `--clean` only deletes.
- System DBs (`_users`, `_replicator`, ...) are dumped by backup, but restore and tools skip them unless `--include-system-dbs` is given. Restoring or deleting them replaces the server's users and replication jobs.
- The tools' `--delete`/`--rebuild`/`--compact` really run on every server version now. Until the `data_size` fix they were silently no-ops on CouchDB 3 and BigCouch, so anyone who scripted them against those servers will now get their DBs deleted.
- `del client[name]` does opposite things in the two libraries: in python-cloudant it only evicts a local cache entry, in CouchDB-Python it sends HTTP DELETE. The code uses the explicit `client.pop(name, None)` (restore) and `client.delete(name)` (tools); keep it that way and never write `del client[...]`.
- The scripts work in the current directory. Backup wipes `./dumps/` (unless `--resume` is given) and writes `./dump.zip`; restore wipes `./unpacked/`. Run them from a scratch directory, not the repo root.
- All three `print(args)` at startup, which echoes `--password`.

## Running and verifying

```bash
python3 -m venv .venv && . .venv/bin/activate   # Debian/Ubuntu need the python3.X-venv package
pip install -r requirements.txt                 # unpinned; resolves to cloudant 2.15.0 and CouchDB 1.2, both final releases
```

- There is no test suite. Offline checks: `pyflakes`, `--help`, and `PYTHONPYCACHEPREFIX=/tmp/pyc python -m py_compile` (py_compile ignores `PYTHONDONTWRITEBYTECODE` and would write `__pycache__/` into the repo, where git doesn't show it). None of them contact a server. Set `PYTHONDONTWRITEBYTECODE=1` for anything else that runs the scripts.
- End to end: start a throwaway server on a free port, e.g. `sudo docker run -d --rm --name cbtest -p 127.0.0.1:5999:5984 -e COUCHDB_USER=admin -e COUCHDB_PASSWORD=admin couchdb:3.5`, then create `_users` and `_replicator`. Seed data shaped like the real workload: Kazoo (2600hz) clusters with thousands of DBs named `account/xx/yy/<hex>-YYYYMM`, monthly DBs of 100k+ docs, and voicemail attachments shared across DBs. BigCouch 0.4 (the older Kazoo backend) also has to keep working.
- To check a change, compare old (`git show HEAD:<script>`) against new. Diff dumps with `PYTHONHASHSEED=0`, because without it the key order inside design docs changes from run to run. Restore into a second empty server and compare docs (ignoring `_rev`) and attachment bytes. Measure peak RSS with `/usr/bin/time -v`.
- CouchDB 3.x has no admin party and `_all_dbs` is admin-only, so pass both `--user` and `--password`. With only one of them, backup and restore silently switch to admin party and send no credentials; credentials embedded in `--host` are ignored too.
- Exit codes:
  - backup: 1 if zip is missing or fails, or if any DB failed; 130 on Ctrl-C during the dump.
  - restore: 1 if any DB failed or had docs or attachments rejected (`[ERR]` lines); 130 on Ctrl-C during the import.
  - tools: 0 even when per-DB actions fail (1 only if listing the DBs fails), so read its output.
  - A Ctrl-C outside the DB loop (connecting, unpacking the zip, zipping) gives a plain Python traceback.

## Dump format: the contract between backup and restore

```
dump.zip                        zip of dumps/, entries at the archive root
  <sha1(db_name)>.json          line 1: DB name; then one JSON doc per line (_all_docs include_docs, design docs included)
  attachments/<sha1(digest)>    attachment bytes; named by sha1 of the stub's "digest" string ("md5-...")
```

- Restore relies on these facts: line 1 is the name, every other line is a doc, each `_attachments` stub has `digest` and `content_type`, and the bytes sit at `attachments/sha1(digest)`. It globs `*.json` and never recomputes the file name. The dump has no version marker, so change both scripts together and keep existing `dump.zip` files restorable.
- Attachments are content-addressed across DBs, so identical content is stored once.
- Backups are shallow: only the winning revision of each live doc. No history, conflicts, deleted docs, `_local` docs or `_security`, and restored DBs are always non-partitioned. Design docs also gain empty `views`/`indexes`/`lists`/`shows` keys and `options: {"partitioned": false}` from python-cloudant's `DesignDocument`.

## Memory: nothing may scale with DB or attachment size

Real DBs are multi-GB, and backup runs 8 threads at once. The old versions were OOM-killed at about 3 GB; the current ones stay around 50 MB for backup and 100–300 MB for restore. Keep it that way:

- **Backup** writes each doc straight to its file and `db.pop`s it, because python-cloudant's `Database.__iter__` caches every doc it yields. It streams attachments with its own `r_session.get(..., stream=True)`: `Document.get_attachment()` buffers the whole body and re-fetches the doc first.
- **Restore** streams each dump file twice through `read_batches()` (~8MB `_bulk_docs` batches). It uploads each attachment with its own PUT that streams the file. The old code read the whole file first, and python-cloudant's `put_attachment` does two extra GETs and doesn't URL-encode the name, so `a?b` became `a`.

## Backup internals

- **Completion markers.** A DB is written to `<sha1>.json.partial` and renamed to `.json` when finished. An attachment goes to `<file>.<thread id>.partial` and is renamed into place, because threads can download the same attachment at once.
- **What `--resume` trusts.** It skips a `.json` only if it has at least one doc line after the name line and ends in `\n`; empty DBs are simply dumped again. This catches the empty, name-only and cut-off files that older versions left when a DB was killed or failed. It can't catch an old file cut off exactly at a line boundary, which is why the README says to do a full run once after upgrading. An untrusted `.json` is deleted before the retry, so a DB that fails again is left out of `dump.zip` instead of being zipped half done. An attachment is trusted only if its size equals the stub's `length`; attachments uploaded with `Content-Encoding: gzip` never match, so they are just downloaded again.
- **Attachment consistency.** The attachment GET passes `?rev=<dumped rev>`, so the file matches the stub even if the doc changes during the backup. If compaction removes that revision first, the GET 404s, the DB fails and `--resume` redoes it. `enforce_content_length` makes urllib3 1.x (the version Python 3.5 installs) raise on a truncated body instead of saving it.
- **zip.** The system `zip` (checked at startup) builds `<realpath of dump.zip>.tmp/dump.zip` from inside `dumps/`, and the result then replaces `dump.zip`. The realpath means a `dump.zip` symlink is updated rather than replaced, and zip's own temp file stays in that `.tmp/` dir, which is cleaned at startup. Don't zip into an existing archive: `zip` merges into it instead of replacing it. The old `dump.zip` survives a failed zip, so the disk needs room for both.
- **Ctrl-C.** A SIGINT handler (`on_ctrl_c`) counts presses in the plain global int `interrupted`. The design choices, each forced by a failure seen in testing:
  - It raises no KeyboardInterrupt, which can land inside the executor's locking and hang it or skip the cleanup.
  - It takes no lock, not even `threading.Event.set()`: on Python 3.14 a second SIGINT microseconds later runs the handler nested inside the first one and deadlocked there.
  - It writes its message to stderr through `write_stderr()`, because with `| tee` the same Ctrl-C kills tee and closes stdout. That is a raw `os.write` that ignores errors, and it is skipped when stderr was closed at startup, since fd 2 may then be a CouchDB socket.
  - The main loop prints through `output()`, which sends stdout to /dev/null once it breaks. An exception out of the loop would skip cancelling the queue, and the executor would then start every queued DB.

  Workers check `interrupted` per doc and per 1MB attachment chunk, and their `finally` blocks remove the partials. The main loop cancels the queued futures the next time it wakes. A second Ctrl-C calls `os._exit(130)`, because there is no HTTP timeout and a hung request never sees the flag. `os._exit` drops buffered output, which is why `output()` flushes every line. The handler isn't installed when SIGINT was ignored at startup (a background job), and the previous handler is put back for the zip step. Signals under ~10 ms apart can be merged into a single call. On Windows the handler may only run when a DB finishes, since lock waits there aren't interrupted by Ctrl-C.

## Restore internals

- **Pass 1** runs before the DB is dropped, so a broken dump fails with the DB untouched. It parses the whole file and checks that every line is a JSON object whose `_id` is a string. It also checks that every attachment stub has `digest` and `content_type` and that its file exists. Keep this order. `--clean` skips pass 1, since it only needs the name line.
- **Pass 2** imports in batches with design docs held back and sent last. Otherwise a restored `validate_doc_update` would reject the docs of the batches after it. With the old single `_bulk_docs` it rejected all the attachment PUTs instead.
- **Rejections.** If the server refuses a whole batch with a 400 or 413 (e.g. one doc over `max_document_size` when moving from BigCouch to CouchDB 3), `bulk_docs()` retries it one doc at a time. Other statuses (401, 404, 429, 5xx...) fail the DB, since retrying per doc would only multiply them. Rejected docs become `[ERR]` lines and the rest of the DB continues. The same goes for attachment PUTs refused with 400/409/412/413/415. Any other attachment error, including a connection error, fails the DB.
- **Revs.** Each attachment PUT uses the rev returned by `_bulk_docs`, chained from one PUT to the next on the same doc.
- **Concurrency.** Restore runs 2 workers (cut from 8 in "Reduce memory leaks") and sleeps 10 ms per DB ("less aggressive restore"). The README Troubleshooting section ties 500s during big restores to server limits (`max_dbs_open`, `LimitNOFILE`), so test against many DBs before raising concurrency.
- **Ctrl-C.** It uses the same kind of handler as backup, and its messages name the DBs in the global `importing` set.
  - A worker adds its DB to `importing` before its last `interrupted` check, so a DB dropped after a Ctrl-C is always named.
  - Queued DBs are cancelled. A DB still in pass 1 returns at its next batch without being dropped, and DBs already dropped finish importing.
  - At exit, restore lists every cancelled DB that its filters would have restored, since there is no `--resume`; the filter lives in `skip_reason()`.
  - After a Ctrl-C, `output()` writes everything to stderr like the handler does: the results of the DBs still finishing, their `[ERR]` lines and the final list. With `| tee`, stdout is gone by then.
  - A second Ctrl-C lists the DBs that may be left incomplete and calls `os._exit(130)`. A third one exits without writing, in case the second is stuck on a stalled stderr.

## Tools

- `get_size` takes the first size that isn't None (not the first truthy one, since 0 is a real size): `sizes.active` (CouchDB ≥ 2, the only one on 3.x), then `data_size` (1.x), then `disk_size` (BigCouch).
- `--rebuild` deletes the DB if it exists and creates it again, so `--delete --rebuild` works. It also resets `_security` and drops the partitioned flag.
- On BigCouch, `--compact` prints "Failed to compact": the clustered port refuses `_compact` (it has to go to the node-local port 5986).
- `--clean` and `--purge` are accepted but not implemented.

## Code conventions

- Each script has a `### Functions` section and an `if __name__ == "__main__":` block. The block sets module globals that the workers read: `args`, `client`, `interrupted` (backup and restore; an int count of Ctrl-C presses), `importing` (restore), `re_match`/`re_exclude` (only when their flag is given) and the `path_*` variables. The hyphenated file names can't be imported; for offline tests, load them with `importlib.util.spec_from_file_location`, set the globals and call `process_database`.
- Workers return their log lines as one string, and the main thread prints it, so output from different threads doesn't interleave. Restore's `process_database` returns `(log, rejected_count)`. Tools runs sequentially and prints from the function.
- `--match`/`--exclude` use `re.match`, which anchors only at the start: `--match=users` matches `users_old` but not `_users`. `--exclude` is applied after `--match`, and the system-DB skip after both.
- Shared flags are copy-pasted into each script's argparse block, and some same-named flags mean different things. Restore `--clean` drops DBs; tools `--clean` is an unimplemented "delete all docs"; tools `--delete` is closer to restore `--clean`.
- The README promises Python ≥ 3.5, and all three scripts were run on 3.5.10. Don't use f-strings; use `.format()`, `%` or `+`. Follow the surrounding code for the `### <name>` end markers and for print spacing, which is mixed.
- Both client libraries are dead upstream: `cloudant` is deprecated, and its successor `ibmcloudant` has a different API; `CouchDB` stopped at 1.2. The scripts depend on their internals (dict caches, `r_session`, `Document.document_url`), so don't swap or upgrade them as a side task.
- Backup and restore build the `Cloudant` client the same way: `admin_party` when credentials are missing, and `use_basic_auth` for servers with `require_valid_user = true`. Keep the two in sync.

## Git

- `.gitignore` is `/*` with exceptions only for `.gitignore` and `README.md`; `!.py` matches a file literally named `.py`. Tracked files stay tracked, but any new file needs `git add -f` or its own exception. Stray `__pycache__/`, `dumps/` or `dump.zip` in the repo won't show up in `git status`, so check with `git status --ignored`. Several files have no trailing newline, so `echo ... >> .gitignore` glues lines together.
- Commit subjects are short plain English, usually capitalized, with no prefix or body (e.g. "Reduce memory leaks", "Improve exception logging and skip empty DBs").
