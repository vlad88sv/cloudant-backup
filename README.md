# cloudant-backup

The scripts in this repo can export (dump) and import all DBs from a Cloudant-based database: CouchDB, PouchDB, BigCouch.

# Requirements
This script requires `Python` >= 3.5, `pip3`, `cloudant` python lib.

Pip3: `python3 -m ensurepip`

Cloudant library: `sudo pip3 install cloudant`



# Examples:

## Export

> couchdb-dump.py is to be used when you need to extract/export/dump all the databases to a dump.zip file, for backup

### Example 1
> DB localhost:5984, no credentials, output in dump.zip

`./couchdb-dump.py`

> DB with custom port, no credentials, output in dump.zip

`./couchdb-dump.py --host='http://localhost:1337'`

> DB localhost:5984, with credentials, output in dump.zip

`./couchdb-dump.py --user='admin' --password='admin'`
 
## Import

> couchdb-import.py is to be used when you need to import/recover all the databases from the dump.zip file and write them to a DB server


> DB localhost:5984, no credentials, input from dump.zip

`./couchdb-import.py`

> DB localhost:5984, with credentials, input from dump.zip

`./couchdb-import.py --user='admin' --password='admin'`

> DB localhost:5984, with credentials, input from dump.zip

> --delete matching DBs in target before recreating

`./couchdb-import.py --user='admin' --password='admin' --delete`

