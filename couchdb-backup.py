#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import glob
import hashlib
import shutil
import subprocess
import threading
import concurrent.futures
import re

### Functions
def is_dumped(file_dump):
    # A finished dump has at least one doc after the DB name line and ends with a newline.
    # Versions without --resume left empty, name-only or cut-off files for DBs that were
    # killed or failed; a really empty DB fails this check too, but re-dumping it is cheap.
    try:
        with open(file_dump, "rb") as filehandle:
            filehandle.readline()
            has_documents = filehandle.read(1) != b""
            filehandle.seek(-1, os.SEEK_END)
            return has_documents and filehandle.read(1) == b"\n"
    except OSError:
        return False
### is_dumped

def dump_attachment(document, attachment, attachment_hashed):
    # Attachment files are named after their digest, so a file of the right size already
    # holds this content: another DB dumped it earlier, or the run being resumed did.
    file_attachment = path_attachments + attachment_hashed
    if os.path.isfile(file_attachment) and os.path.getsize(file_attachment) == document["_attachments"][attachment].get('length'):
        return

    # Several threads can fetch the same attachment at once, so each one writes its own
    # .partial file and renames it into place when complete.
    file_partial = "{}.{}.partial".format(file_attachment, threading.get_ident())
    try:
        with open(file_partial, "wb") as filehandle:
            document.get_attachment(attachment, write_to=filehandle, attachment_type='binary')
        os.replace(file_partial, file_attachment)
    finally:
        if os.path.isfile(file_partial):
            os.remove(file_partial)
### dump_attachment

def process_database(database):

    if args.match and not re_match.match(database):
        return "No match for DB " + database + ""

    if args.exclude and re_exclude.match(database):
        return "Excluding match for DB " + database + ""

    database_hashed = hashlib.sha1(database.encode('utf-8')).hexdigest()
    file_dump = path_dump + "{}.json".format(database_hashed)

    if args.resume and is_dumped(file_dump):
        return "Already dumped DB " + database + ", skipping"

    # An unfinished file from an interrupted run; without it, a failure below leaves the DB
    # out of dump.zip instead of zipping it half done
    if os.path.isfile(file_dump):
        os.remove(file_dump)

    log_buffer = []
    log_buffer.append('Dumping: {} - {}'.format(database_hashed, database))

    # The DB goes to a .partial file that is renamed to .json only once complete,
    # so --resume can trust that every .json file is a finished dump.
    file_partial = file_dump + ".partial"
    db = client[database]
    try:
        with open(file_partial, "w") as filehandle:
            filehandle.write(database + "\n")
            for document in db:
                filehandle.write(json.dumps(document) + "\n")
                if "_attachments" in document:
                    for attachment in document["_attachments"]:
                        attachment_hashed = hashlib.sha1(document["_attachments"][attachment]['digest'].encode('utf-8')).hexdigest()
                        log_buffer.append('└ attachment: {} - {}'.format(attachment, attachment_hashed))
                        dump_attachment(document, attachment, attachment_hashed)
                # Iterating a cloudant DB caches every document it yields; drop each one
                # once written so memory doesn't grow with the size of the DB.
                db.pop(document['_id'], None)
        os.replace(file_partial, file_dump)
    finally:
        db.clear()
        if os.path.isfile(file_partial):
            os.remove(file_partial)
    return "\n".join(log_buffer)

### Functions

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--match', help='Regular expression to match the DB names. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression to match the DB names for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--resume', help='Keep the DBs already dumped in ./dumps/ by an interrupted run and dump only the missing ones. Use the same --match/--exclude as that run. Default: false', action="store_true")

    args = parser.parse_args()
    print(args)

    # dump.zip is built with the system zip; check for it now rather than after the whole dump
    if not shutil.which("zip"):
        print ("The zip command was not found, install it first (e.g. sudo apt install zip)")
        sys.exit(1)

    path = os.getcwd()
    path_dump = path + "/dumps/"
    path_attachments = path + "/dumps/attachments/"
    # realpath so a dump.zip symlink (e.g. to a NAS) gets updated instead of replaced by a file
    file_zip = os.path.realpath(path + "/dump.zip")
    path_zip = file_zip + ".tmp/"
    print ("DB dump to be stored at %s" % path_dump)

    # Left by a run killed while compressing, and can be as big as dump.zip itself
    shutil.rmtree(path_zip, ignore_errors=True)

    if args.resume:
        # Leftovers of a run killed mid-write; those DBs and attachments are dumped again
        for file_partial in glob.glob(glob.escape(path_dump) + "*.partial") + glob.glob(glob.escape(path_attachments) + "*.partial"):
            os.remove(file_partial)
        print ("Resuming: DBs already dumped at %s will be skipped" % path_dump)
    elif os.path.isdir(path_dump):
        shutil.rmtree(path_dump)

    if not os.path.isdir(path_attachments):
        try:
            os.makedirs(path_attachments)
        except OSError:
            print ("Creation of the directory %s failed" % path_attachments)
        else:
            print ("Successfully created the directory %s " % path_attachments)

    from cloudant.client import Cloudant
    client = Cloudant(args.user,
                      args.password,
                      url=args.host,
                      admin_party=not (args.user and args.password),
                      use_basic_auth=(args.user and args.password),
                      connect=True,
                      auto_renew=True
                    )

    session = client.session()
    if session:
        print('Username: {0}'.format(session.get('userCtx', {}).get('name')))

    if args.match:
        re_match = re.compile(args.match)
        print ('Regular expresion will be used to filter databases')

    if args.exclude:
        re_exclude = re.compile(args.exclude)
        print ('Regular expresion will be used to filter databases for exclusion')

    print ("===")

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_import = {executor.submit(process_database, database): database for database in client.all_dbs()}
        for future in concurrent.futures.as_completed(future_import):
            file = future_import[future]
            try:
                data = future.result()
            except Exception as exc:
                failed += 1
                print('%r generated an exception: %s' % (file, exc))
            else:
                print(data)

    # Flush so zip's output doesn't land before ours when stdout goes to a file
    print ("Compressing DUMP folder", flush=True)
    # zip adds to an existing archive instead of replacing it, so build a new one in its own
    # directory (zip puts its temp file there too) and move it over dump.zip only when zip
    # succeeds; if it fails, the previous dump.zip is left untouched.
    os.makedirs(path_zip)
    result = subprocess.run(["zip", "-r", "-q", "-dg", "-ds", "100m", path_zip + "dump.zip", "."], cwd=path_dump)
    if result.returncode == 0:
        os.replace(path_zip + "dump.zip", file_zip)
    shutil.rmtree(path_zip)
    if result.returncode != 0:
        print ("zip failed with exit code %d, dump.zip was not updated" % result.returncode)
        sys.exit(1)

    if failed:
        print ("%d DBs failed and are missing from dump.zip, run again with --resume to retry only those" % failed)
        sys.exit(1)

    print ("all done!")
