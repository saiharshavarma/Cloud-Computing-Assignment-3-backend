import os
import json
import logging
import boto3

from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

logger = logging.getLogger()
logger.setLevel(logging.INFO)

region       = os.environ.get('AWS_REGION', 'us-east-1')
service      = 'es'
host         = os.environ['ES_ENDPOINT']
index_name   = os.environ.get('OPENSEARCH_INDEX', 'photos')

session     = boto3.Session()
credentials = session.get_credentials().get_frozen_credentials()
awsauth     = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    region,
    service,
    session_token=credentials.token
)

# OpenSearch client 
es = OpenSearch(
    hosts=[{'host': host, 'port': 443}],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection
)

# AWS service clients
rekognition = boto3.client('rekognition', region_name=region)
s3          = boto3.client('s3', region_name=region)


def lambda_handler(event, context):
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key    = record['s3']['object']['key']
        # URL-encoded spaces come in as '+'
        key    = key.replace('+', ' ')

        # 1) Rekognition labels
        resp = rekognition.detect_labels(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            MaxLabels=10,
            MinConfidence=75
        )
        auto_labels = [lbl['Name'].lower() for lbl in resp.get('Labels', [])]

        # 2) Custom labels from metadata header
        head = s3.head_object(Bucket=bucket, Key=key)
        logger.info("S3 Metadata for %s: %s", key, head.get('Metadata', {}))
        meta = head.get('Metadata', {})
        custom = []
        if 'customlabels' in meta:
            custom = [
                lbl.strip().lower()
                for lbl in meta['customlabels'].split(',')
                if lbl.strip()
            ]

        # 3) Combine & de-dupe
        labels = list(set(auto_labels + custom))

        # 4) Build document
        doc = {
            'objectKey'       : key,
            'bucket'          : bucket,
            'createdTimestamp': record.get('eventTime'),
            'labels'          : labels
        }

        # 5) Index into OpenSearch
        es.index(index=index_name, id=f'{bucket}/{key}', body=doc)
        logger.info(f"Indexed {key}: {labels}")

    return {
        'statusCode': 200,
        'body'      : json.dumps({'message': 'Indexing complete'})
    }