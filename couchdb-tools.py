#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import json
import os
import hashlib
import shutil
import glob
import time
import gc
import concurrent.futures
import couchdb
from furl import furl
import humanize
import re

### Functions
def get_json_documents(lines):
    documents = []
    for line in lines:
        documents.append(json.loads(line.strip()))
    return documents
### get_json_documents

def get_size(database):
    info = client[database].info()
    # sizes.active on CouchDB >= 2 (the only one on 3.x), data_size on 1.x, only disk_size on BigCouch
    for size in ((info.get('sizes') or {}).get('active'), info.get('data_size'), info.get('disk_size')):
        if size is not None:
            return humanize.naturalsize(size)
    return humanize.naturalsize(0)
### get_size

def is_server_side(exc):
    # CouchDB-Python raises ServerError((status, (error, reason))) for statuses without a class
    # of their own: 5xx, but also 400 or 413, which would fail every request the same way
    try:
        return exc.args[0][0] >= 500
    except (IndexError, TypeError):
        return False
### is_server_side

def clean_database(database):
    # Deletes every doc except design docs (the views) and returns how many docs were deleted
    # and how many couldn't be. It walks _changes?style=all_docs, which lists each doc's leaf
    # revisions without the bodies: the winner first, then the conflicts, deleted ones last.
    # Every leaf of a live doc is deleted: a live conflict left behind would become the new
    # winner. Deleting an already deleted leaf just adds another tombstone, which is cheaper
    # than fetching each doc to tell its live leaves apart (and BigCouch ignores conflicts=true
    # when _all_docs is given keys). Only a doc with more than 100 leaves is fetched with its
    # _conflicts, so that just its live leaves are deleted: BigCouch takes about a second per
    # edit of such a doc. The deletions show up again later in the feed, marked deleted, and
    # are skipped.
    deleted = 0
    # Docs with conflicts that couldn't be deleted: kept by id and skipped when the feed lists
    # them again (any leaf that did go gives them a new seq), which would otherwise loop over
    # them. A single-leaf doc that failed doesn't come back, so a count does for those.
    failed = set()
    failed_single = 0
    # The first server error behind a failure, for the report
    first_error = None
    since = 0
    # Pages aim at about 20000 revisions, going by the previous page: up to 1000 docs, fewer
    # when docs have many leaves. The first one is small, since nothing is known about the DB.
    limit = 10
    try:
        db = client[database]
        while True:
            changes = db.changes(style='all_docs', since=since, limit=limit)
            if not changes['results']:
                return deleted, len(failed) + failed_single, first_error
            since = changes['last_seq']
            leaves = sum(len(change['changes']) for change in changes['results'])
            limit = max(10, min(1000, 20000 * len(changes['results']) // leaves))
            winners = []
            conflicts = []
            for change in changes['results']:
                if change['id'].startswith('_design/') or change.get('deleted') or change['id'] in failed:
                    continue
                revs = [leaf['rev'] for leaf in change['changes']]
                if len(revs) > 100:
                    try:
                        document = db.get(change['id'], conflicts=True)
                    except couchdb.http.ServerError as exc:
                        if not is_server_side(exc):
                            raise
                        first_error = first_error or exc
                        failed_single += 1
                        continue
                    if document is None:
                        continue
                    revs = [document.rev] + document.get('_conflicts', [])
                winners.append({'_id': change['id'], '_rev': revs[0], '_deleted': True})
                conflicts += [{'_id': change['id'], '_rev': rev, '_deleted': True} for rev in revs[1:]]
            # Conflicts go first, 100 per request (BigCouch takes quadratic time over many edits
            # of one doc in a request), and a doc whose conflicts couldn't all be deleted keeps
            # its winner rather than showing an old conflicting revision instead
            failures = set()
            for start in range(0, len(conflicts), 100):
                chunk = conflicts[start:start + 100]
                try:
                    results = db.update(chunk)
                except couchdb.http.ServerError as exc:
                    # e.g. BigCouch timing out on a doc with thousands of leaves
                    if not is_server_side(exc):
                        raise
                    first_error = first_error or exc
                    failures.update(stub['_id'] for stub in chunk)
                    continue
                for success, docid, rev_or_exc in results:
                    if not success:
                        failures.add(docid)
            winners_left = [winner for winner in winners if winner['_id'] not in failures]
            try:
                results = db.update(winners_left) if winners_left else []
            except couchdb.http.ServerError as exc:
                # BigCouch may still be working through an earlier timed-out chunk
                if not is_server_side(exc):
                    raise
                first_error = first_error or exc
                failures.update(winner['_id'] for winner in winners_left)
                results = []
            for success, docid, rev_or_exc in results:
                if not success:
                    failures.add(docid)
            deleted += len(winners) - len(failures)
            conflicted = set(stub['_id'] for stub in conflicts)
            for docid in failures:
                if docid in conflicted:
                    failed.add(docid)
                else:
                    failed_single += 1
    except Exception as exc:
        raise Exception("{} docs deleted before {!r}".format(deleted, exc)[:1000])
### clean_database

def purge_database(database):
    # Purges every doc except design docs (the views), deleted ones and conflicts included, and
    # returns how many. _changes?style=all_docs gives every leaf revision. Requests stay within
    # CouchDB 2.3's limits of 100 docs and 1000 revisions (3.x has none by default); a doc with
    # more leaves than that is purged over several requests.
    purged = 0
    since = 0
    batch = {}
    batch_revs = 0
    # Docs whose last revisions are in the batch, counted when it's posted
    batch_docs = 0
    try:
        db = client[database]
        while True:
            changes = db.changes(style='all_docs', since=since, limit=100)
            for change in changes['results']:
                if change['id'].startswith('_design/'):
                    continue
                revs = [leaf['rev'] for leaf in change['changes']]
                for start in range(0, len(revs), 1000):
                    chunk = revs[start:start + 1000]
                    if len(batch) == 100 or batch_revs + len(chunk) > 1000:
                        db.resource.post_json('_purge', body=batch)
                        purged += batch_docs
                        batch = {}
                        batch_revs = 0
                        batch_docs = 0
                    batch[change['id']] = chunk
                    batch_revs += len(chunk)
                    if start + 1000 >= len(revs):
                        batch_docs += 1
            # Posted before the next page: a doc purged only in part moves to a later seq, and
            # the feed would list it again
            if batch:
                db.resource.post_json('_purge', body=batch)
                purged += batch_docs
                batch = {}
                batch_revs = 0
                batch_docs = 0
            if not changes['results']:
                break
            since = changes['last_seq']
    except Exception as exc:
        raise Exception("{} docs purged before {!r}".format(purged, exc)[:1000])
    return purged
### purge_database

def process_database(database):
    # Returns whether the DB was processed; skipped DBs don't contact the server
    if args.match and not re_match.match(database):
        return False

    if args.exclude and re_exclude.match(database):
        return False

    # --delete, --rebuild, --clean or --purge on these would wipe the server's users and replication jobs
    if database.startswith('_') and not args.include_system_dbs:
        print ("Skipping system DB " + database + ", use --include-system-dbs to include it")
        return False

    buffer = []
    initial_size = get_size(database)
    if args.delete:
        try:
            client.delete(database)
            buffer.append("Deleted: " + database)
        except Exception as exc:
            buffer.append("Failed to delete: {}: {!r}".format(database, exc))

    if args.rebuild:
        try:
            # Already gone if --delete was given too
            if database in client:
                client.delete(database)
            client.create(database)
        except Exception as exc:
            buffer.append("Failed to rebuild: {}: {!r}".format(database, exc))

    if args.clean:
        try:
            deleted, failed, first_error = clean_database(database)
            buffer.append("Cleaned: {} ({} docs deleted{}{})".format(database, deleted, ", {} could not be".format(failed) if failed else "", "; first server error: {!r}".format(first_error)[:500] if first_error else ""))
        except Exception as exc:
            buffer.append("Failed to clean: {}: {}".format(database, exc))

    if args.purge:
        try:
            buffer.append("Purged: {} ({} docs)".format(database, purge_database(database)))
        except Exception as exc:
            buffer.append("Failed to purge: {}: {}".format(database, exc))

    if args.compact:
        try:
            client[database].cleanup()
            client[database].compact()
        except Exception as exc:
            buffer.append("Failed to compact: {}: {!r}".format(database, exc))
    
    try:
        buffer.append (database + ' ::: ' + initial_size + ' -> ' + get_size(database) + ' ::: ' +  client[database].resource.url)
    except Exception:
        pass

    print ("\n".join(buffer))
    return True
### process_database

### Functions

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--delete', help='Delete matching DBs, and not recreate them.', action="store_true")
    parser.add_argument('--rebuild', help='Delete DB and recreate it again', action="store_true")
    parser.add_argument('--clean', help='Delete all docs in matching DBs, preserves views (design docs). Deleted docs leave tombstones, which replicate', action="store_true")
    parser.add_argument('--purge', help='Purge all docs in matching DBs, deleted ones included, preserves views (design docs). Nothing is left to replicate; add --compact to free the disk space. Needs CouchDB 2.3+ or 1.x (not BigCouch)', action="store_true")
    parser.add_argument('--compact', help='Cleanup and Compact all docs in matching DBs', action="store_true")
    parser.add_argument('--match', help='Regular expression to match the DB names. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression to match the DB names for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--include-system-dbs', help='Also act on system DBs such as _users and _replicator. Default: false', action="store_true")
    parser.add_argument('--timeout', help='Seconds to wait for the server on each request before giving up on it. 0 waits forever. Default: 300', type=float, default=300)

    args = parser.parse_args()
    if not 0 <= args.timeout <= 1000000:
        parser.error("--timeout must be between 0 and 1000000 seconds")
    print(args)


    url = furl(args.host)
    url.username = args.user
    url.password = args.password
    client = couchdb.Server(str(url), session=couchdb.Session(timeout=args.timeout or None))

    # Filter databases
    if args.match:
        re_match = re.compile(args.match)
        print ('Regular expresion will be used to filter databases')

    if args.exclude:
        re_exclude = re.compile(args.exclude)
        print ('Regular expresion will be used to filter databases for exclusion')

    databases = list(client)
    unreachable = 0
    for database in databases:
        try:
            # Skipped DBs leave the count as it is
            if process_database(database):
                unreachable = 0
        except Exception as exc:
            print ("Failed: {}: {!r}".format(database, exc))
            # Timeouts and connection errors; a server that stopped answering would cost a
            # --timeout per remaining DB
            unreachable = unreachable + 1 if isinstance(exc, OSError) else 0
            if unreachable == 3:
                print ("Stopping: 3 DBs in a row couldn't reach the server")
                sys.exit(1)