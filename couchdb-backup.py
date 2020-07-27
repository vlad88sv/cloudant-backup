#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import hashlib
import shutil
import concurrent.futures

### Functions
def process_database(database):
    log_buffer = []
    database_hashed = hashlib.sha1(database.encode('utf-8')).hexdigest()
    log_buffer.append('Dumping: {} - {}'.format(database_hashed, database))
    filehandle = open(path_dump + "/{}.json".format(database_hashed), "a")
    filehandle.truncate(0)
    filehandle.write(database + "\n")

    buffer = ""
    for document in client[database]:
        buffer += json.dumps(document) + "\n"
        if "_attachments" in document:
            for attachment in document["_attachments"]:
                attachment_hashed = hashlib.sha1(document["_attachments"][attachment]['digest'].encode('utf-8')).hexdigest()
                log_buffer.append('└ attachment: {} - {}'.format(attachment, attachment_hashed))
                file_attachment = open(path_attachments + attachment_hashed, "wb")
                document.get_attachment(attachment, write_to=file_attachment, attachment_type='binary')
                file_attachment.close()
    
    filehandle.write(buffer)
    filehandle.close()
    return "\n".join(log_buffer)

### Functions

if __name__ == "__main__":
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_import = {executor.submit(process_database, database): database for database in client.all_dbs()}
        for future in concurrent.futures.as_completed(future_import):
            file = future_import[future]
            try:
                data = future.result()
            except Exception as exc:
                print('%r generated an exception: %s' % (file, exc))
            else:
                print(data)
    
    print ("Compressing DUMP folder")
    shutil.make_archive("dump", 'zip', path_dump)

    print ("all done!")