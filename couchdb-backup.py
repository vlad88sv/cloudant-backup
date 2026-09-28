#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import glob
import hashlib
import shutil
import signal
import subprocess
import threading
import concurrent.futures
import re
import requests.exceptions
from urllib.parse import quote

# Errors that mean the server didn't answer (timeouts included) rather than refused something
NETWORK_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)

### Functions
def compile_pattern(parser, flag, pattern):
    # --match/--exclude take a regular expression, not a shell wildcard. '*name*' is the usual
    # mistake and isn't valid, so the error suggests the regex the wildcard stands for.
    try:
        return re.compile(pattern)
    except re.error as exc:
        message = "{} {!r} is not a valid regular expression ({})".format(flag, pattern, exc)
        if '*' in pattern:
            try:
                re.compile(pattern.replace('*', '.*'))
                message += "; for a wildcard, use {}='{}'".format(flag, pattern.replace('*', '.*'))
            except re.error:
                pass
        parser.error(message)
### compile_pattern

def on_ctrl_c(signum, frame):
    # Counts presses in a plain global that workers check per doc and attachment chunk. It
    # raises nothing (a KeyboardInterrupt can land inside the executor's locking and break it)
    # and takes no lock: a second Ctrl-C can run this handler nested inside the first one.
    # A second Ctrl-C quits at once, for when a request to a server that stopped answering
    # never returns.
    global interrupted
    interrupted += 1
    if interrupted > 1:
        os._exit(130)
    write_stderr("\nInterrupted, stopping the DBs in progress (Ctrl-C again to quit now). Run again with --resume to continue\n")
### on_ctrl_c

def write_stderr(text):
    # stderr, since with `| tee` the same Ctrl-C kills tee and closes stdout. Raw os.write, as
    # the handler can run while the main thread is inside print(). Skipped when stderr was
    # closed at startup (2>&-): fd 2 may then belong to a CouchDB connection.
    if sys.stderr is not None:
        try:
            os.write(2, text.encode('utf-8'))
        except OSError:
            pass
### write_stderr

def output(text):
    # When stdout breaks (`| tee` killed by Ctrl-C), the rest goes to /dev/null instead of
    # raising out of the main loop, which would skip cancelling the queue and fail at exit
    try:
        print(text, flush=True)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
### output

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
    # Streamed in 1MB chunks: document.get_attachment() would hold the whole attachment in
    # memory, and fetch the document again first. The rev makes the content match the stub
    # just dumped even if the doc changed since.
    response = document.r_session.get(document.document_url + "/" + quote(attachment, safe=''), params={'rev': document['_rev']}, stream=True)
    try:
        response.raise_for_status()
        # urllib3 1.x, what Python 3.5 gets, would otherwise save a body cut short without error
        response.raw.enforce_content_length = True
        with open(file_partial, "wb") as filehandle:
            for chunk in response.iter_content(1024 * 1024):
                if interrupted or stopped:
                    raise RuntimeError("Interrupted")
                filehandle.write(chunk)
        os.replace(file_partial, file_attachment)
    finally:
        response.close()
        if os.path.isfile(file_partial):
            os.remove(file_partial)
### dump_attachment

def process_database(database):
    # Returns the log of the DB and whether it contacted the server (skipped DBs don't)

    # A queued DB picked up between the Ctrl-C (or a stop) and the main loop cancelling the queue
    if interrupted or stopped:
        raise RuntimeError("Interrupted")

    if args.match and not re_match.match(database):
        return "No match for DB " + database + "", False

    if args.exclude and re_exclude.match(database):
        return "Excluding match for DB " + database + "", False

    database_hashed = hashlib.sha1(database.encode('utf-8')).hexdigest()
    file_dump = path_dump + "{}.json".format(database_hashed)

    if args.resume and is_dumped(file_dump):
        return "Already dumped DB " + database + ", skipping", False

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
                # Set on Ctrl-C (or a stop); the .partial file is removed below and --resume redoes the DB
                if interrupted or stopped:
                    raise RuntimeError("Interrupted")
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
    return "\n".join(log_buffer), True

### Functions

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--match', help='Regular expression (not a wildcard) matched from the start of each DB name. Example ".*provisioner" or ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression (not a wildcard) matched from the start of each DB name, for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--resume', help='Keep the DBs already dumped in ./dumps/ by an interrupted run and dump only the missing ones. Use the same --match/--exclude as that run. Default: false', action="store_true")
    parser.add_argument('--timeout', help='Seconds to wait for the server on each request before failing that DB. 0 waits forever. Default: 300', type=float, default=300)

    args = parser.parse_args()
    if not 0 <= args.timeout <= 1000000:
        parser.error("--timeout must be between 0 and 1000000 seconds")
    print(args)

    # Checked before anything else: an invalid --match used to fail only after ./dumps/ was wiped
    if args.match:
        re_match = compile_pattern(parser, '--match', args.match)
        print ('Regular expresion will be used to filter databases')

    if args.exclude:
        re_exclude = compile_pattern(parser, '--exclude', args.exclude)
        print ('Regular expresion will be used to filter databases for exclusion')

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
                      auto_renew=True,
                      # Without it a server that stops answering blocks a worker for good
                      timeout=args.timeout or None
                    )

    session = client.session()
    if session:
        print('Username: {0}'.format(session.get('userCtx', {}).get('name')))


    print ("===")

    interrupted = 0
    # Set when the server stops answering; workers stop on it like on a Ctrl-C
    stopped = False
    previous_sigint = signal.getsignal(signal.SIGINT)
    # A background job started with SIGINT ignored keeps ignoring it
    if previous_sigint is not signal.SIG_IGN:
        signal.signal(signal.SIGINT, on_ctrl_c)
    # Output is flushed as it goes: a second Ctrl-C quits with os._exit, which drops the buffer
    sys.stdout.flush()
    failed = 0
    # DBs in a row that failed to reach the server; skipped DBs leave it as it is
    unreachable = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_import = {executor.submit(process_database, database): database for database in client.all_dbs()}
        for future in concurrent.futures.as_completed(future_import):
            if not interrupted and not stopped:
                file = future_import[future]
                try:
                    data, contacted = future.result()
                except Exception as exc:
                    failed += 1
                    output('%r generated an exception: %s' % (file, exc))
                    unreachable = unreachable + 1 if isinstance(exc, NETWORK_ERRORS) else 0
                else:
                    output(data)
                    if contacted:
                        unreachable = 0
                # A server that stopped answering would cost a --timeout per remaining DB. With
                # nothing left in the queue there's nothing to save, and dump.zip is still built.
                # Kept apart from the Ctrl-C count, so a Ctrl-C after it is still a first one.
                if unreachable == 3 and any(not queued.done() and not queued.running() for queued in future_import):
                    output("Stopping: 3 DBs in a row couldn't reach the server. Waiting for the DBs in progress to finish or time out; run again with --resume once it answers")
                    stopped = True
            if interrupted or stopped:
                # Otherwise the executor would still start every queued DB before exiting
                for queued in future_import:
                    queued.cancel()
                break

    if stopped:
        sys.exit(1)

    if interrupted:
        sys.exit(130)

    # Ctrl-C now stops zip and the script right away, as before
    signal.signal(signal.SIGINT, previous_sigint)

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
