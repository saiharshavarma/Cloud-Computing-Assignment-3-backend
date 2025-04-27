import os
import json
import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

region       = 'us-east-1'
bot_id       = os.environ['BOT_ID']
alias_id     = os.environ['BOT_ALIAS_ID']
locale       = 'en_US'
host         = os.environ['ES_ENDPOINT']
index_name   = os.environ['OPENSEARCH_INDEX']
s3_bucket    = os.environ['S3_BUCKET']

# Change this if you ever host your front-end on a different domain:
ALLOWED_ORIGIN  = 'https://my-photo-frontend-ss18851.s3.amazonaws.com'
ALLOWED_HEADERS = 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,x-amz-meta-customlabels'
ALLOWED_METHODS = 'GET,PUT,POST,OPTIONS'

# SigV4 auth for OpenSearch
session = boto3.Session()
creds   = session.get_credentials().get_frozen_credentials()
awsauth = AWS4Auth(creds.access_key, creds.secret_key,
                   region, 'es', session_token=creds.token)

es = OpenSearch(
    hosts=[{'host': host, 'port': 443}],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30,
    max_retries=3,
    retry_on_timeout=True
)

# Lex V2 runtime client
lexv2 = boto3.client('lexv2-runtime', region_name=region)


def _cors_headers():
    return {
        'Access-Control-Allow-Origin' : ALLOWED_ORIGIN,
        'Access-Control-Allow-Headers': ALLOWED_HEADERS,
        'Access-Control-Allow-Methods': ALLOWED_METHODS
    }


def lambda_handler(event, context):
    method = event.get('httpMethod', '')

    # 1) Handle CORS preflight
    if method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': _cors_headers(),
            'body': ''
        }

    # 2) Only GET is supported here
    if method != 'GET':
        return {
            'statusCode': 405,
            'headers': _cors_headers(),
            'body': json.dumps({'message': f'Unsupported method: {method}'})
        }

    # 3) Extract & validate query string
    q = (event.get('queryStringParameters') or {}).get('q', '').strip()
    if not q:
        return {
            'statusCode': 400,
            'headers': _cors_headers(),
            'body': json.dumps({'message': "Missing query parameter 'q'"})
        }

    # 4) Call Lex to interpret
    resp  = lexv2.recognize_text(
        botId=bot_id,
        botAliasId=alias_id,
        localeId=locale,
        sessionId=context.aws_request_id,
        text=q
    )
    interp   = resp['interpretations'][0]
    raw      = interp.get('inputTranscript', q)
    keywords = [w.lower() for w in raw.replace(',', ' ').split() if w]

    # 5) Query OpenSearch 
    results = []
    if keywords:
        body = {
            "query": {
                "bool": {
                    "should": [
                        { "match": { "labels": kw } }
                        for kw in keywords
                    ]
                }
            }
        }
        search_resp = es.search(index=index_name, body=body, size=50)
        for hit in search_resp['hits']['hits']:
            src = hit['_source']
            key = src['objectKey']
            url = f"https://{s3_bucket}.s3.amazonaws.com/{key}"
            results.append({
                "url":    url,
                "labels": src.get('labels', [])
            })

    # 6) Return results with CORS headers
    return {
        'statusCode': 200,
        'headers': _cors_headers(),
        'body': json.dumps({'results': results})
    }