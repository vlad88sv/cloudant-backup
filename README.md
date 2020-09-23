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

## Restore examples

> couchdb-restore.py is to be used when you need to import/recover all the databases from the dump.zip file and write them to a DB server

### b.1 DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py`

### b.2 DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin'`

### b.3 Clean DBs, DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin' --clean`

This flag will **delete** all DBs listed in the backup, without further action.

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