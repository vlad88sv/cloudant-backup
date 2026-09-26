#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import io
import json
import os
import sys
import hashlib
import shutil
import glob
import time
import concurrent.futures
import re
import logging
import signal
from urllib.parse import quote
import requests.exceptions
from requests.exceptions import HTTPError
from cloudant.client import Cloudant
from cloudant.document import Document
from cloudant.error import CloudantClientException

# Errors that mean the server didn't answer (timeouts included) rather than refused something
NETWORK_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)

# Docs are sent to _bulk_docs in requests of about this size, so memory doesn't depend on
# the size of the DB and requests stay far below the server's request size limit
BULK_DOCS_SIZE = 8 * 1024 * 1024

### Functions
def on_ctrl_c(signum, frame):
    # Counts presses in a plain global that workers check before dropping a DB, so the DBs
    # already dropped are finished. It raises nothing (a KeyboardInterrupt can land inside the
    # executor's locking and break it) and takes no lock: a second Ctrl-C can run this handler
    # nested inside the first one. A second Ctrl-C quits at once, for when a request to a server
    # that stopped answering never returns. Messages go to stderr, since with `| tee` the same
    # Ctrl-C closes stdout.
    global interrupted
    interrupted += 1
    if interrupted > 1:
        # A third press quits even if the second one is stuck writing to a stalled stderr
        if interrupted == 2:
            write_stderr("\nQuitting now, these DBs may be left incomplete: {}\n".format(", ".join(sorted(importing)) or "none"))
        os._exit(130)
    write_stderr("\nInterrupted, finishing the DBs being imported: {}. The rest are not restored (Ctrl-C again to quit now)\n".format(", ".join(sorted(importing)) or "none"))
### on_ctrl_c

def write_stderr(text):
    # Raw os.write, as the handler can run while the main thread is inside print(). Skipped when
    # stderr was closed at startup (2>&-): fd 2 may then belong to a CouchDB connection.
    if sys.stderr is not None:
        try:
            os.write(2, text.encode('utf-8'))
        except OSError:
            pass
### write_stderr

def output(text):
    # After a Ctrl-C everything goes to stderr like the Ctrl-C messages, since with `| tee` the
    # same Ctrl-C kills tee. If stdout breaks anyway, the rest goes to /dev/null instead of
    # raising out of the main loop and failing at exit.
    if interrupted:
        write_stderr(text + "\n")
        return
    try:
        print(text, flush=True)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
### output

def read_batches(file):
    # Yields the docs of a dump file in batches of about BULK_DOCS_SIZE; line 1 is the DB name
    batch = []
    batch_size = 0
    with open(file, 'r') as filehandle:
        filehandle.readline()
        for number, line in enumerate(filehandle, 2):
            try:
                document = json.loads(line)
            except ValueError as exc:
                raise ValueError("{} line {}: {}".format(os.path.basename(file), number, exc))
            if not isinstance(document, dict) or not isinstance(document.get('_id'), str):
                raise ValueError("{} line {}: not a document with an _id".format(os.path.basename(file), number))
            batch.append(document)
            batch_size += len(line)
            if batch_size >= BULK_DOCS_SIZE:
                yield batch
                batch = []
                batch_size = 0
    if batch:
        yield batch
### read_batches

def hash_attachment(stub):
    return hashlib.sha1(stub['digest'].encode('utf-8')).hexdigest()
### hash_attachment

def check_attachments(documents):
    for document in documents:
        for attachment in document.get('_attachments', {}):
            stub = document['_attachments'][attachment]
            if 'digest' not in stub or 'content_type' not in stub or not os.path.isfile(path_attachments + hash_attachment(stub)):
                raise Exception("Attachment '{}' of doc '{}' is incomplete or missing from the dump".format(attachment, document['_id']))
### check_attachments

def bulk_docs(database, documents):
    # When the server refuses the whole request because of a doc in it (400 bad doc, 413 doc over
    # max_document_size), the docs are sent one at a time so only the bad ones fail. Other errors
    # (401, 404, 429...) have nothing to do with the docs and fail the DB.
    try:
        # Sent from a stream instead of database.bulk_docs(): a single send of the ~8MB batch
        # would have to finish within --timeout, while blocks each get their own
        response = database.r_session.post(database.database_url + "/_bulk_docs", data=io.BytesIO(json.dumps({'docs': documents}).encode('utf-8')), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        return response.json()
    except HTTPError as exc:
        if exc.response is None or exc.response.status_code not in (400, 413):
            raise
        if len(documents) == 1:
            return [{'id': documents[0]['_id'], 'error': exc.response.status_code, 'reason': str(exc)}]
    results = []
    for document in documents:
        results += bulk_docs(database, [document])
    return results
### bulk_docs

def put_attachment(database, document_id, rev, attachment, content_type, attachment_hashed):
    # Instead of python-cloudant's put_attachment, which reads the whole file into memory,
    # fetches the doc before and after, and doesn't quote the name ('a?b' would become 'a')
    url = Document(database, document_id).document_url + "/" + quote(attachment, safe='')
    with open(path_attachments + attachment_hashed, "rb") as filehandle:
        response = database.r_session.put(url, params={'rev': rev}, headers={'Content-Type': content_type}, data=filehandle)
    response.raise_for_status()
    return response.json()['rev']
### put_attachment

def bulk_import(database, documents, buffer):
    # Appends its log lines to buffer and returns how many docs and attachments were rejected
    errors = 0
    attachments = {}

    if not documents:
        return errors

    for document in documents:
        document.pop('_rev', None)
        if '_attachments' in document:
            attachments[document['_id']] = document.pop('_attachments')
        ### if
    ### for document

    revs = {}
    for check in bulk_docs(database, documents):
        if 'error' in check:
            errors += 1
            buffer.append("└ [ERR] {} in doc '{}'. {}".format(check['error'], check['id'], check['reason']))
        else:
            revs[check['id']] = check['rev']
    ### for check

    for document_id in attachments:
        # The doc failed above and its error is already logged
        if document_id not in revs:
            continue
        for attachment in attachments[document_id]:
            stub = attachments[document_id][attachment]
            attachment_hashed = hash_attachment(stub)
            buffer.append('└ {} - {}'.format(attachment, attachment_hashed))
            try:
                revs[document_id] = put_attachment(database, document_id, revs[document_id], attachment, stub['content_type'], attachment_hashed)
            except HTTPError as exc:
                # Only statuses about this attachment; anything else (401, 404, 429, 5xx...)
                # would hit every attachment after it too, so it fails the DB
                if exc.response is None or exc.response.status_code not in (400, 409, 412, 413, 415):
                    raise
                errors += 1
                buffer.append("└ [ERR] attachment '{}' of doc '{}'. {}".format(attachment, document_id, exc))
        ### for attachment
    ### for document_id

    return errors
### bulk_import

def read_name(file):
    with open(file, 'r') as filehandle:
        return filehandle.readline().strip()
### read_name

def skip_reason(file, database_name):
    # Why the DB in this dump file isn't restored, or None if it is
    if not database_name:
        return "Skipping empty DB from file " + file

    if args.match and not re_match.match(database_name):
        return "No match for DB " + database_name + ""

    if args.exclude and re_exclude.match(database_name):
        return "Excluding match for DB " + database_name + ""

    # Restoring or deleting these would replace the server's users and replication jobs
    if database_name.startswith('_') and not args.include_system_dbs:
        return "Skipping system DB " + database_name + ", use --include-system-dbs to include it"

    return None
### skip_reason

def process_database(file):
    # Returns the log of the DB and how many docs and attachments were rejected, or None when
    # the DB was skipped without contacting the server
    database_name = read_name(file)
    reason = skip_reason(file, database_name)
    if reason:
        return reason, None

    # Parse the whole file and check its attachments before touching the server, so a broken
    # dump fails before its DB is dropped
    count = 0
    if not args.clean:
        for batch in read_batches(file):
            # The DB hasn't been dropped yet, so on Ctrl-C (or a stop) it's left as it is
            if interrupted or stopped:
                return "Not restored: " + database_name, None
            count += len(batch)
            check_attachments(batch)

    # Named by the Ctrl-C messages. Added before the last check, so a Ctrl-C landing between
    # the two still names this DB if it goes on to be dropped.
    importing.add(database_name)
    try:
        if interrupted or stopped:
            return "Not restored: " + database_name, None

        try:
            client.delete_database(database_name)
        except CloudantClientException as exc:
            # A DB that doesn't exist yet is fine; on any other error the import would merge into the old DB
            if exc.status_code != 404:
                raise

        if args.clean:
            return "Cleaned: " + database_name, 0

        buffer = []
        buffer.append("Importing DB: {} - {} [{}]".format(os.path.basename(file), database_name, count))
        errors = 0
        database = client.create_database(database_name)

        # Design docs go last: a validate_doc_update in one would reject the docs in the batches
        # after it, which didn't happen when the whole DB went in a single _bulk_docs
        design_documents = []
        for batch in read_batches(file):
            design_documents += [document for document in batch if document['_id'].startswith('_design/')]
            errors += bulk_import(database, [document for document in batch if not document['_id'].startswith('_design/')], buffer)
        errors += bulk_import(database, design_documents, buffer)

        # Drops python-cloudant's cached object for the DB, not the DB itself
        client.pop(database_name, None)
    finally:
        importing.discard(database_name)

    time.sleep(0.01);

    return "\n".join(buffer), errors
### process_database

### Functions

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--dumpfile', help='Path of the dump to restore. Default: dump.zip', default='dump.zip')
    parser.add_argument('--clean', help='Delete matching DBs, and not recreate them. Default: false', action="store_true")
    parser.add_argument('--match', help='Regular expression to match the DB names. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression to match the DB names for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--include-system-dbs', help='Also restore (or with --clean, delete) system DBs such as _users and _replicator, replacing the ones on the server. Default: false', action="store_true")
    parser.add_argument('--timeout', help='Seconds to wait for the server on each request before failing that DB. 0 waits forever. Default: 300', type=float, default=300)

    args = parser.parse_args()
    if not 0 <= args.timeout <= 1000000:
        parser.error("--timeout must be between 0 and 1000000 seconds")
    print(args)

    path = os.getcwd()
    path_unpacked = path + "/unpacked/"
    path_attachments = path + "/unpacked/attachments/"
    print ("DB dump to be unpacked at %s" % path_unpacked)

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

    if args.match:
        re_match = re.compile(args.match)
        print ('Regular expresion will be used to filter databases')

    if args.exclude:
        re_exclude = re.compile(args.exclude)
        print ('Regular expresion will be used to filter databases for exclusion')

    if os.path.isdir(path_unpacked):
        shutil.rmtree(path_unpacked)

    if not os.path.isdir(path_unpacked):
        try:
            os.makedirs(path_unpacked)
        except OSError:
            print ("Creation of the directory %s failed" % path_unpacked)
        else:
            print ("Successfully created the directory %s " % path_unpacked)

    print ("===")

    shutil.unpack_archive(args.dumpfile, path_unpacked)

    files = glob.glob(path_unpacked + "*.json")

    failed = 0
    # DBs in a row that failed to reach the server; skipped DBs leave it as it is
    unreachable = 0
    stopped = False
    interrupted = 0
    importing = set()
    # A background job started with SIGINT ignored keeps ignoring it
    if signal.getsignal(signal.SIGINT) is not signal.SIG_IGN:
        signal.signal(signal.SIGINT, on_ctrl_c)
    # Output is flushed as it goes: a second Ctrl-C quits with os._exit, which drops the buffer
    sys.stdout.flush()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_import = {executor.submit(process_database, file): file for file in files}
        queued_cancelled = False
        for future in concurrent.futures.as_completed(future_import):
            if (interrupted or stopped) and not queued_cancelled:
                # Queued DBs would return right away anyway; this saves starting each of them
                for queued in future_import:
                    queued.cancel()
                queued_cancelled = True
            if future.cancelled():
                continue
            file = future_import[future]
            try:
                data, errors = future.result()
            except Exception as exc:
                failed += 1
                logging.exception('%s (%r) generated an exception: %s' % (read_name(file), file, exc))
                unreachable = unreachable + 1 if isinstance(exc, NETWORK_ERRORS) else 0
            else:
                output(data)
                if errors:
                    failed += 1
                if errors is not None:
                    unreachable = 0
            # A server that stopped answering would cost a --timeout per remaining DB. Stops like
            # a Ctrl-C: the DBs not dropped yet are left alone. Pointless with nothing queued. Kept
            # apart from the Ctrl-C count, so a Ctrl-C after it is still a first one.
            if unreachable == 3 and not interrupted and not stopped and any(not queued.done() and not queued.running() for queued in future_import):
                output("Stopping: 3 DBs in a row couldn't reach the server. Waiting for the DBs in progress to finish or time out")
                stopped = True

    if failed:
        output("%d DBs failed or had docs or attachments rejected, see the errors above" % failed)

    if interrupted or stopped:
        # Restore has no --resume, so list what still needs restoring
        for future in future_import:
            if future.cancelled() and not skip_reason(future_import[future], read_name(future_import[future])):
                output("Not restored: " + read_name(future_import[future]))
        sys.exit(1 if stopped else 130)

    if failed:
        sys.exit(1)
