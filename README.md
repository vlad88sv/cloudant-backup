# cloudant-backup

The Python scripts in this repo can backup (*export*) and restore (*import*) all DBs from a Cloudant-based database: CouchDB, PouchDB, BigCouch.

## Features
- Based on the official [Cloudant Python lib](https://github.com/cloudant/python-cloudant)
- Exports ALL databases, but can filter by name using RegEx
- Exports everything into a single compressed ZIP file
- Supports any attachments (export/import) without issues
- Fast import: multithreaded bulk_docs process
- Convenient: few dependencies, singles files

## Why yet another backup tool?
| Tool                                                                                       | Limitation                                        |
|--------------------------------------------------------------------------------------------|---------------------------------------------------|
| [cloudant/couchbackup](https://github.com/cloudant/couchbackup)                            | Single DB, no attachments                         |
| [docs@maintenance/backups](https://docs.couchdb.org/en/latest/maintenance/backups.html)    | Manual process, requires root acces to filesystem |
| [danielebailo/couchdb-dump](https://github.com/danielebailo/couchdb-dump)                  | Single DB, no attachments                         |
| [pouchdb-community/pouchdb-dump-cli](https://github.com/pouchdb-community/pouchdb-dump-cli)| Single DB, no attachments                         |
| [raffi-minassian/couchdb-dump](https://github.com/raffi-minassian/couchdb-dump)            | Deprecated, Single DB, no attachments             |


## Requirements
This script requires `Python` >= 3.5, `pip3`, and the `cloudant` python lib.

Install pip3: `sudo python3 -m ensurepip` or check your distro packages.

Install all requirements: `sudo pip3 install -r requirements.txt`

`couchdb-backup.py` also needs the `zip` command to create `dump.zip` (`sudo apt install zip` on Debian/Ubuntu; on Windows, put Info-ZIP's `zip.exe` on the `PATH`). It checks for it at startup. The new `dump.zip` is built next to the previous one, which is only replaced once zip succeeds, so leave free disk space for both.

## Important note
This tool does a shallow backup, meaning that it is only backing up the latest revision of the docss in the databases. This is a faster, but a less complete backup.

If you run into issues (error 500) please see the [troubleshooting](#troubleshooting) section

# Examples:

## Remote backup
If:
- your remote server doesn't meet the requirements for the script to be run
- or you plan on doing external backup (example: pulling DBs daily from another server)
- or want a local DB dump for development purposes

You can do that via [SSH remote forwarding](https://www.ssh.com/ssh/tunneling/example#remote-forwarding)

Example: DB server is **couchdb.myserver.com** - couch runs in port 5984 with firewall rules that avoid you to reach the DB directly at **http://couchdb.myserver.com:5984**

You can access it locally via:

`ssh -R 5984:localhost:1337 root@couchdb.myserver.com -p22`

Where:

- **5984** is the remote DB port
- **1337** is the port in your localhost that will tunnel the traffic to the DB
- **22** is the SSH port of your server

Your new URL to reach the DB will be: **http://localhost:1337**

## Backup examples

> couchdb-backup.py is to be used when you need to extract/export/dump all the databases to a dump.zip file

### a.1 DB localhost:5984, no credentials, output in dump.zip

`./couchdb-backup.py`

### a.2 DB with custom port, no credentials, output in dump.zip

`./couchdb-backup.py --host='http://localhost:1337'`

### a.3 DB localhost:5984, with credentials, output in dump.zip

`./couchdb-backup.py --user='admin' --password='admin'`
 
### a.4 DB localhost:5984, with credentials, output in dump.zip, filter by database name using RegEx

`./couchdb-backup.py --user='admin' --password='admin' --match='.*-myprogram|users|.*bkp.*'`

`--match` and `--exclude` (in all three scripts) are regular expressions matched from the start of the DB name, not shell wildcards: for the DBs whose name contains `provisioner` use `--match='.*provisioner'`, not `'*provisioner*'`.

### a.5 Resume an interrupted backup

`./couchdb-backup.py --user='admin' --password='admin' --resume`

Run it from the same directory, with the same `--match`/`--exclude` as the interrupted run. DBs already in `./dumps/` are skipped, only the missing or unfinished ones are dumped, and `dump.zip` is created again at the end. Don't resume a `./dumps/` left by a version of the script without `--resume`: it can't always tell which of those DBs were left unfinished, so run a full backup once after upgrading.

If some DBs fail with an error, the backup still creates `dump.zip` without them, lists them, and exits with code 1; `--resume` retries only those.

Ctrl-C stops a backup within seconds with exit code 130: the DBs in progress are discarded and `dump.zip` is left as it was. Continue it later with `--resume`. If the server stopped answering, press Ctrl-C again to quit at once; otherwise the stuck requests give up after `--timeout` seconds.

## Restore examples

> couchdb-restore.py is to be used when you need to import/recover all the databases from the dump.zip file and write them to a DB server

Each DB in the dump is **deleted** on the server and then imported again. System DBs such as `_users` and `_replicator` are skipped unless you add `--include-system-dbs`, since restoring them replaces the server's users and replication jobs.

Ctrl-C stops a restore, with exit code 130, after the DBs being imported at that moment are finished, so none is left half imported. It names those DBs, and every DB it didn't touch gets a `Not restored: <db>` line (if the server stopped answering, the DBs being imported fail after `--timeout` instead and may be left incomplete). From the Ctrl-C on, all output goes to stderr, which still reaches the terminal when stdout is piped to `tee`. Press Ctrl-C again to quit at once; it names the DBs that may be left incomplete. The restore exits with code 1 if any DB failed or had docs or attachments rejected (`[ERR]` lines).

### b.1 DB localhost:5984, no credentials, input from dump.zip

`./couchdb-restore.py`

### b.2 DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin'`

### b.3 Clean DBs, DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin' --clean`

This flag will **delete** all DBs listed in the backup (except system DBs, unless `--include-system-dbs` is given), without further action.

## Tools examples

> couchdb-tools.py reports the size of each DB and can delete, rebuild, clean, purge or compact the matching ones. System DBs such as `_users` and `_replicator` are skipped unless you add `--include-system-dbs`. Without `--match`, every DB on the server is affected.

### c.1 Size of every DB

`./couchdb-tools.py --user='admin' --password='admin'`

### c.2 Delete every doc but keep the views (design docs), then compact

`./couchdb-tools.py --user='admin' --password='admin' --match='.*-2019..' --clean --compact`

Deleted docs leave tombstones, which replicate to other servers.

### c.3 Purge every doc but keep the views, then compact

`./couchdb-tools.py --user='admin' --password='admin' --match='.*-2019..' --purge --compact`

Purged docs are removed completely, deleted ones included, and nothing is replicated. `--compact` doesn't work on BigCouch's clustered port 5984. Needs CouchDB 1.x or 2.3+; BigCouch and CouchDB 2.0–2.2 refuse it (reported as "Failed to purge"). On CouchDB 3.x, views may keep rows for purged docs, a server-side issue with automatic compaction, so prefer `--clean` where views matter or rebuild them afterwards.

### Timeout

All three scripts take `--timeout` (seconds, default 300, 0 waits forever). A request the server doesn't answer in time fails the DB it belongs to instead of blocking forever; one while connecting or listing the DBs ends the run. If 3 DBs in a row fail because the server can't be reached, the script stops with exit code 1 instead of spending a timeout on every remaining DB (for a backup, continue later with `--resume`; a restore lists the DBs it didn't touch). DBs skipped by `--match`/`--exclude` don't count.

## Troubleshooting

### Linux

### Ubuntu 20.04 + CouchDB installed from Snap

1. Edit `/etc/systemd/system/snap.couchdb.server.service` and add `LimitNOFILE=64000` under section `[Service]`
2. Edit `/var/snap/couchdb/5/etc/local.ini` and add `max_dbs_open = 2500` under section `[couchdb]`
3. Reload systemctl and restart couchdb: `sudo systemctl daemon-reload && sudo service snap.couchdb.server restart`

### Windows

2. Edit CouchDB's `local.ini` and add `max_dbs_open = 2500` under section `[couchdb]`
3. Disable Antivirus (even Windows Defender) or exclude CouchDB, temporally during the import operation
4. Restart the CouchDB service