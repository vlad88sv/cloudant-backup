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

parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
parser.add_argument('--user', help='DB username. Default: none')
parser.add_argument('--password', help='DB password. Default: none')
parser.add_argument('--dumpfile', help='Regular expression to match the DB names. Default: dump.zip', default='dump.zip')
parser.add_argument('--delete', dest='delete', help='Delete matching DBs before saving imported data. Default: false', default=False, action="store_true")

args = parser.parse_args()
print(args)

path = os.getcwd()
path_unpacked = path + "/unpacked/"
path_attachments = path + "/unpacked/attachments/"
print ("DB dump to be unpacked at %s" % path_unpacked)

from cloudant.client import Cloudant
client = Cloudant(args.user, args.password, url=args.host, admin_party= not (args.user and args.password), connect=True)

session = client.session()
if session:
    print('Username: {0}'.format(session.get('userCtx', {}).get('name')))

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

for file in files:
    filehandle = open(file, 'r') 
    lines = filehandle.readlines() 
    database_name = lines[0].strip()
    print("Importing DB: {} - {}".format(database_name, file))    
    if args.delete:
        client.delete_database(database_name)
        time.sleep(1)

    database = client.create_database(database_name)
    
    # From line 1 (starting at 0), are all documents.
    for line in lines[1:]:
        document_json = json.loads(line.strip())
        attachments = document_json.get('_attachments', None)
        document_json.pop('_attachments', None)
        try:
            document = database.create_document(document_json)            
        except OSError as err:
            print ("Bad document or view found. {}/{} - {}".format(database_name, document_json['_id'], err))
        
        del document_json

        if attachments:
            print ("  ...Importing attachments for document " + document['_id'])
            for attachment in attachments:
                attachment_hashed = hashlib.sha1(attachments[attachment]['digest'].encode('utf-8')).hexdigest()
                print('    -> {} - {}'.format(attachment, attachment_hashed))
                file_attachment = open(path_attachments + attachment_hashed, "rb")
                document.put_attachment(attachment, attachments[attachment]['content_type'], file_attachment.read())
                file_attachment.close()
        ### if attachments
        document.clear()
    ### for line
    del lines
    database.clear()
    filehandle.close()
### for file    