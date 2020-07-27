# cloudant-backup

The Python scripts in this repo can backup (*export*) and restore (*import*) all DBs from a Cloudant-based database: CouchDB, PouchDB, BigCouch.

## Features
- Based on the official [Cloudant Python lib](https://github.com/cloudant/python-cloudant)
- Exports ALL databases
- Exports everything into a single compressed ZIP file
- Supports any attachments (export/import) without issues
- Fast import: multithreaded bulk_docs processs
- Convenient: few dependencies, singles files

## Requirements
This script requires `Python` >= 3.5, `pip3`, `cloudant` python lib.

Pip3: `python3 -m ensurepip`

Cloudant library: `sudo pip3 install cloudant`

## Important notes
This toold does a shallow backup, meanting that it is only backing up the latest revision of the docss in the databases. This is a faster, but less complete backup.

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
 
## Restore examples

> couchdb-restore.py is to be used when you need to import/recover all the databases from the dump.zip file and write them to a DB server

### b.1 DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py`

### b.2 DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin'`

### b.3 Clean DBs, DB localhost:5984, with credentials, input from dump.zip

`./couchdb-restore.py --user='admin' --password='admin' --clean`

This flag will **delete** all DBs listed in the backup, without further action.
