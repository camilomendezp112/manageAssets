import json
import os
import uuid
import boto3
import sentry_sdk
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME')
table = dynamodb.Table(TABLE_NAME)

sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    traces_sample_rate=1.0
)

sentry_sdk.set_tag("module", "manageAsset")
sentry_sdk.set_tag("team", "grupo-3")

def lambda_handler(event, context):
    try:
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        if not claims:
            # Fallback for HTTP API structure just in case
            claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
        tenant_id = claims.get('custom:tenant_id') or claims.get('tenant_id')
        
        if not tenant_id:
            return {
                'statusCode': 403,
                'body': json.dumps({'error': 'Unauthorized: tenant_id not found in token'})
            }

        # Determine the HTTP method (Supports both REST API and HTTP API formats)
        http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')
        
        if http_method == 'POST':
            return handle_post(event, tenant_id)
        elif http_method == 'PUT':
            return handle_put(event, tenant_id)
        elif http_method == 'DELETE':
            return handle_delete(event, tenant_id)
        else:
            return {
                'statusCode': 405,
                'body': json.dumps({'error': f'Method {http_method} not allowed'})
            }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }

def handle_post(event, tenant_id):
    body = json.loads(event.get('body', '{}'))
    
    name = body.get('name')
    asset_type = body.get('type')
    status = body.get('status', 'ACTIVE')
    user_id = body.get('user_id')
    modelo = body.get('modelo')
    
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
        'created_at': created_at,
        'updated_at': created_at
    }
    
    if user_id:
        item['user_id'] = user_id
    if modelo:
        item['modelo'] = modelo
    
    table.put_item(Item=item)
    
    return {
        'statusCode': 201,
        'body': json.dumps({'message': 'Asset created successfully', 'asset': item})
    }

def handle_put(event, tenant_id):
    body = json.loads(event.get('body', '{}'))
    asset_id = body.get('id')
    
    if not asset_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing required field: id'})
        }

    update_expr = []
    expr_attr_values = {}
    expr_attr_names = {}
    
    updatable_fields = ['name', 'type', 'status', 'user_id', 'modelo']
    
    for field in updatable_fields:
        if field in body:
            update_expr.append(f"#{field} = :{field}")
            expr_attr_names[f"#{field}"] = field
            expr_attr_values[f":{field}"] = body[field]
            
    if not update_expr:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'No fields provided to update'})
        }
        
    # Agregamos la actualización automática de fecha
    updated_at = datetime.utcnow().isoformat()
    update_expr.append("#updated_at = :updated_at")
    expr_attr_names["#updated_at"] = "updated_at"
    expr_attr_values[":updated_at"] = updated_at
        
    update_expression = "SET " + ", ".join(update_expr)

    try:
        response = table.update_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"ASSET#{asset_id}"
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ConditionExpression="attribute_exists(PK)",
            ReturnValues="ALL_NEW"
        )
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Asset updated successfully', 'asset': response.get('Attributes')})
        }
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return {
            'statusCode': 404,
            'body': json.dumps({'error': 'Asset not found'})
        }

def handle_delete(event, tenant_id):
    body = json.loads(event.get('body', '{}'))
    asset_id = body.get('id')
    
    if not asset_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing required field: id'})
        }

    try:
        table.delete_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"ASSET#{asset_id}"
            },
            ConditionExpression="attribute_exists(PK)"
        )
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Asset deleted successfully'})
        }
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return {
            'statusCode': 404,
            'body': json.dumps({'error': 'Asset not found'})
        }
