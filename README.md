# cloudant-backup

The scripts in this repo can export (dump) and import all DBs from a Cloudant-based database: CouchDB, PouchDB, BigCouch.

# Requirements
This script requires `Python` >= 3.5, `pip3`, `cloudant` python lib.

Pip3: `python3 -m ensurepip`

Cloudant library: `sudo pip3 install cloudant`

# Examples:

## Export

> couchdb-dump.py is to be used when you need to extract/export/dump all the databases to a dump.zip file, for backup

### Example a.1
> DB localhost:5984, no credentials, output in dump.zip

`./couchdb-dump.py`

### Example a.2

> DB with custom port, no credentials, output in dump.zip

`./couchdb-dump.py --host='http://localhost:1337'`

### Example a.3

> DB localhost:5984, with credentials, output in dump.zip

`./couchdb-dump.py --user='admin' --password='admin'`
 
## Import

> couchdb-import.py is to be used when you need to import/recover all the databases from the dump.zip file and write them to a DB server

### Example b.1
> DB localhost:5984, no credentials, input from dump.zip

`./couchdb-import.py`

### Example b.2

> DB localhost:5984, with credentials, input from dump.zip

`./couchdb-import.py --user='admin' --password='admin'`

## Clean DBs

> DB localhost:5984, with credentials, input from dump.zip

`./couchdb-import.py --user='admin' --password='admin' --clean`

This flag will **delete** all DBs listed in the backup, without further action.
