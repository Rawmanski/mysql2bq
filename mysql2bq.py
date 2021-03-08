#!/usr/bin/env python

import MySQLdb
from google.cloud import bigquery
import logging
import os
from MySQLdb.converters import conversions
import click
import MySQLdb.cursors
from google.cloud.exceptions import ServiceUnavailable

bqTypeDict = { 'int' : 'INTEGER',
               'varchar' : 'STRING',
               'double' : 'FLOAT',
               'tinyint' : 'INTEGER',
               'decimal' : 'FLOAT',
               'text' : 'STRING',
               'smallint' : 'INTEGER',
               'char' : 'STRING',
               'bigint' : 'INTEGER',
               'float' : 'FLOAT',
               'longtext' : 'STRING',
               'datetime' : 'TIMESTAMP'
              }

def conv_date_to_timestamp(str_date):
    import time
    import datetime

    date_time = MySQLdb.times.DateTime_or_None(str_date)
    unix_timestamp = (date_time - datetime.datetime(1970,1,1)).total_seconds()

    return unix_timestamp

def Connect(host, database, user, password):
    ## fix conversion. datetime as str and not datetime object
    conv=conversions.copy()
    conv[12]=conv_date_to_timestamp
    return MySQLdb.connect(host=host, db=database, user=user, passwd=password,
        conv=conv, cursorclass=MySQLdb.cursors.SSCursor, charset='utf8', use_unicode=True)


def BuildSchema(host, database, user, password, table):
    logging.debug('build schema for table {} in database {}'.format(table, database))
    conn = Connect(host, database, user, password)
    cursor = conn.cursor()
    cursor.execute("DESCRIBE {};".format{table})

    tableDecorator = cursor.fetchall()
    schema = []

    for col in tableDecorator:
        colType = col[1].split("(")[0]
        if colType not in bqTypeDict:
            logging.warning("Unknown type detected, using string: {}".format(str(col[1])))

        field_mode = "NULLABLE" if col[2] == "YES" else "REQUIRED"
        field = bigquery.SchemaField(col[0], bqTypeDict.get(colType, "STRING"), mode=field_mode)

        schema.append(field)

    return tuple(schema)


def bq_load(table, data, max_retries=5):
    logging.info("Sending request")
    uploaded_successfully = False
    num_tries = 0

    while not uploaded_successfully and num_tries < max_retries:
        try:
            insertResponse = table.insert_data(data)

            for row in insertResponse:
                if 'errors' in row:
                    logging.error('not able to upload data: {}'.format(row['errors']))

            uploaded_successfully = True
        except ServiceUnavailable as e:
            num_tries += 1
            logging.error('insert failed with exception trying again retry {}'.format(num_tries))
        except Exception as e:
            num_tries += 1
            logging.error('not able to upload data: {}'.format(str(e)))


@click.command()
@click.option('-h', '--host', default='127.0.0.1', help='MySQL hostname')
@click.option('-d', '--database', required=True, help='MySQL database')
@click.option('-u', '--user', default='root', help='MySQL user')
@click.option('-p', '--password', default='', help='MySQL password')
@click.option('-t', '--table', required=True, help='MySQL table')
@click.option('-i', '--projectid', required=True, help='Google BigQuery Project ID')
@click.option('-n', '--dataset', required=True, help='Google BigQuery Dataset name')
@click.option('-l', '--limit',  default=0, help='max num of rows to load')
@click.option('-s', '--batch_size',  default=1000, help='max num of rows to load')
@click.option('-k', '--key',  default='google_key.json', help='Location of google service account key (relative to current working dir)')
@click.option('-v', '--verbose',  default=0, count=True, help='verbose')
def SQLToBQBatch(host, database, user, password, table, projectid, dataset, limit, batch_size, key, verbose):
    # set to max verbose level
    verbose = verbose if verbose < 3 else 3
    loglevel = logging.ERROR - (10 * verbose)

    logging.basicConfig(level=loglevel)

    logging.info("Starting SQLToBQBatch. Got: Table: {}, Limit: {}".format(table, limit))

    ## set env key to authenticate application
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = "{}/{}".format((os.getcwd(), key))

    # Instantiates a client
    bigquery_client = bigquery.Client()

    try:
        bq_dataset = bigquery_client.dataset(dataset)

        # Creates the new dataset
        bq_dataset.create()
        logging.info("Added Dataset")
    except Exception, e:
        if ("Already Exists: " in str(e)):
            logging.info("Dataset already exists")
        else:
            logging.error("Error creating dataset: {} Error".format(str(e)))

    try:
        bq_table = bq_dataset.table(table)
        bq_table.schema = BuildSchema(host, database, user, password, table)
        bq_table.create()

        logging.info("Added Table {}".format(table))
    except Exception, e:
        logging.info(e)
        if ("Already Exists: " in str(e)):
            logging.info("Table {} already exists".format(table))
        else:
            logging.error("Error creating table {}: {} Error".format(table, str(e)))

    conn = Connect(host, database, user, password)
    cursor = conn.cursor()

    logging.info("Starting load loop")
    cursor.execute("SELECT * FROM {}".format((table)))

    cur_batch = []
    count = 0

    for row in cursor:
        count += 1

        if limit != 0 and count >= limit:
            logging.info("limit of {:d} rows reached".format(limit))
            break

        cur_batch.append(row)

        if count % batch_size == 0 and count != 0:
            bq_load(bq_table, cur_batch)

            cur_batch = []
            logging.info("processed {:n} rows".format(count))

    # send last elements
    bq_load(bq_table, cur_batch)
    logging.info("Finished ({:i} total)".format(count))



if __name__ == '__main__':
    ## run the command
    SQLToBQBatch()
