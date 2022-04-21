#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import hashlib
import shutil
import glob
import time
import gc
import concurrent.futures
import re
import logging
from cloudant.client import Cloudant

### Functions
def get_json_documents(lines):
    documents = []
    for line in lines:
        documents.append(json.loads(line.strip()))
    return documents
### get_json_documents

def bulk_import(database, documents):
    buffer = []
    attachments = {}

    for document in documents:
        document.pop('_rev', None)
        if '_attachments' in document:
            attachments[document['_id']] = document['_attachments']
            document.pop('_attachments', None)
        ### if
    ### for document

    checks = database.bulk_docs(documents)

    for check in checks:
        if 'error' in check:
            buffer.append("└ [ERR] {} in doc '{}'. {}".format(check['error'], check['id'], check['reason']))
    ### for check

    if attachments:
        for document in attachments:
            for attachment in attachments[document]:
                attachment_hashed = hashlib.sha1(attachments[document][attachment]['digest'].encode('utf-8')).hexdigest()
                buffer.append('└ {} - {}'.format(attachment, attachment_hashed))
                file_attachment = open(path_attachments + attachment_hashed, "rb")
                database[document].put_attachment(attachment, attachments[document][attachment]['content_type'], file_attachment.read())
                file_attachment.close()
            ### for attachment
        ### for document
    ### if attachments

    return buffer
### bulk_import

def process_database(file):
    buffer = []
    filehandle = open(file, 'r') 
    lines = filehandle.readlines() 
    filehandle.close()

    if not lines:
        return "Skipping empty DB from file " + file

    database_name = lines[0].strip()

    if args.match and not re_match.match(database_name):
        return "No match for DB " + database_name + ""

    if args.exclude and re_exclude.match(database_name):
        return "Excluding match for DB " + database_name + ""

    # From line 1 (starting at 0), are all documents.
    documents = get_json_documents(lines[1:])

    buffer.append("Importing DB: {} - {} [{}]".format(os.path.basename(file), database_name, len(documents)))
    try:
        client.delete_database(database_name)
    except:
        pass
    
    if args.clean:
        return "Cleaned: " + database_name

    database = client.create_database(database_name)
    
    buffer += bulk_import (database, documents)

    del database
    del client[database_name]

    return "\n".join(buffer)
### process_database

### Functions

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
    parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
    parser.add_argument('--user', help='DB username. Default: none')
    parser.add_argument('--password', help='DB password. Default: none')
    parser.add_argument('--dumpfile', help='Regular expression to match the DB names. Default: dump.zip', default='dump.zip')
    parser.add_argument('--clean', help='Delete matching DBs, and not recreate them. Default: false', action="store_true")
    parser.add_argument('--match', help='Regular expression to match the DB names. Example ".*-myprogram|users|.*bkp.*". Default: None.')
    parser.add_argument('--exclude', help='Regular expression to match the DB names for exclusion. Example ".*-myprogram|users|.*bkp.*". Default: None.')

    args = parser.parse_args()
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_import = {executor.submit(process_database, file): file for file in files}
        for future in concurrent.futures.as_completed(future_import):
            file = future_import[future]
            try:
                data = future.result()
            except Exception as exc:
                logging.exception('%r generated an exception: %s' % (file, exc))
            else:
                print(data)