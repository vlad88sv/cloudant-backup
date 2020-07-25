#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import hashlib
import shutil

parser = argparse.ArgumentParser(description='Dumps CouchDB / Bigcouch databases')
parser.add_argument('--host', help='FQDN or IP, including port. Default: http://localhost:5984', default='http://localhost:5984')
parser.add_argument('--user', help='DB username. Default: none')
parser.add_argument('--password', help='DB password. Default: none')
parser.add_argument('--match', help='Regular expression to match the DB names. Default: None.')

args = parser.parse_args()
print(args)

path = os.getcwd()
path_dump = path + "/dumps/"
path_attachments = path + "/dumps/attachments/"
print ("DB dump to be stored at %s" % path_dump)

if os.path.isdir(path_dump):
    shutil.rmtree(path_dump)

if not os.path.isdir(path_attachments):
    try:
        os.makedirs(path_attachments)
    except OSError:
        print ("Creation of the directory %s failed" % path_attachments)
    else:
        print ("Successfully created the directory %s " % path_attachments)

from cloudant.client import Cloudant
client = Cloudant(args.user, args.password, url=args.host, admin_party= not (args.user and args.password), connect=True)

session = client.session()
if session:
    print('Username: {0}'.format(session.get('userCtx', {}).get('name')))

print ("===")

for database in client.all_dbs():
    database_hashed = hashlib.sha1(database.encode('utf-8')).hexdigest()
    print('Dumping: {} - {}'.format(database_hashed, database))
    filehandle = open(path_dump + "/{}.json".format(database_hashed), "a")
    filehandle.truncate(0)
    filehandle.write(database + "\n")

    buffer = ""
    for document in client[database]:
        buffer += json.dumps(document) + "\n"
        if "_attachments" in document:
            print (" ... Dumping attachments")
            for attachment in document["_attachments"]:
                attachment_hashed = hashlib.sha1(document["_attachments"][attachment]['digest'].encode('utf-8')).hexdigest()
                print('   Dumping attachment: {} - {}'.format(attachment, attachment_hashed))
                file_attachment = open(path_attachments + attachment_hashed, "wb")
                document.get_attachment(attachment, write_to=file_attachment, attachment_type='binary')
                file_attachment.close()
    
    filehandle.write(buffer)
    filehandle.close()

print ("Compressing DUMP folder")
shutil.make_archive("dump", 'zip', path_dump)

print ("all done!")