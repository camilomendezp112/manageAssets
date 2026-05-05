import json
import os
import uuid
import boto3
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME')
table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    try:
        # Extraer tenant_id del JWT autorizador
        claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
        tenant_id = claims.get('custom:tenant_id') or claims.get('tenant_id')
        
        if not tenant_id:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Unauthorized: tenant_id not found in token'})
            }

        body = json.loads(event.get('body', '{}'))
        
        name = body.get('name')
        asset_type = body.get('type')
        status = body.get('status', 'ACTIVE')
        user_id = body.get('user_id')
        
        if not name or not asset_type:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required fields: name, type'})
            }

        asset_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()
        
        item = {
            'PK': f"TENANT#{tenant_id}",
            'SK': f"ASSET#{asset_id}",
            'id': asset_id,
            'name': name,
            'type': asset_type,
            'status': status,
            'user_id': user_id,
            'created_at': created_at
        }
        
        table.put_item(Item=item)
        
        return {
            'statusCode': 201,
            'body': json.dumps({'message': 'Asset created successfully', 'asset': item})
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }
