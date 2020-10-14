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

def process_database(database):
    if args.match and not re_match.match(database):
        return "No match for DB " + database + ""

    if args.exclude and re_exclude.match(database):
        return "Excluding match for DB " + database + ""

    buffer = []
    initial_size = humanize.naturalsize(client[database].info()['data_size'])
    if args.delete:
        try:
            del client[database]
            buffer.append("Deleted: " + database)
        except:
            buffer.append("Failed to delete: " + database)
    
    if args.rebuild:
        try:
            del client[database]
            client.create(database)
        except:
            buffer.append("Failed to delete: " + database)

    if args.compact:
        try:
            client[database].cleanup()
            client[database].compact()
        except:
            buffer.append("Failed to compact: " + database)
    
    try:
        buffer.append (database + ' ::: ' + initial_size + ' -> ' + humanize.naturalsize(client[database].info()['data_size']) + ' ::: ' +  client[database].resource.url)
    except Exception:
        pass

    print ("\n".join(buffer))
### process_database

### Functions

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--delete', help='Delete matching DBs, and not recreate them.', action="store_true")
    parser.add_argument('--rebuild', help='Delete DB and recreate it again', action="store_true")
    parser.add_argument('--clean', help='Delete all docs in matching DBs, preserves views', action="store_true")
    parser.add_argument('--purge', help='Purge all docs in matching DBs', action="store_true")
    parser.add_argument('--compact', help='Cleanup and Compact all docs in matching DBs', action="store_true")
    parser.add_argument('--match', help='Regular expression to match the DB names. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression to match the DB names for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')

    args = parser.parse_args()
    print(args)


    url = furl(args.host)
    url.username = args.user
    url.password = args.password
    client = couchdb.Server(str(url))

    # Filter databases
    if args.match:
        re_match = re.compile(args.match)
        print ('Regular expresion will be used to filter databases')

    if args.exclude:
        re_exclude = re.compile(args.exclude)
        print ('Regular expresion will be used to filter databases for exclusion')

    databases = list(client)
    for database in databases:
        try:
            process_database(database)
        except Exception as exc:
            print (exc)