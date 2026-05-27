import json
import os
import uuid
import boto3
from datetime import datetime
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr

# Sentry is optional - if not installed in Lambda package, skip it gracefully
try:
    import sentry_sdk
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN", ""),
        traces_sample_rate=1.0
    )
    sentry_sdk.set_tag("module", "manageAsset")
    sentry_sdk.set_tag("team", "grupo-3")
    SENTRY_ENABLED = True
except ImportError:
    SENTRY_ENABLED = False

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME')
table = dynamodb.Table(TABLE_NAME)

s3_client = boto3.client('s3')
BUCKET_NAME = os.environ.get('BUCKET_NAME')


def capture_exception(e):
    if SENTRY_ENABLED:
        sentry_sdk.capture_exception(e)


def make_response(success, code, data):
    return {
        'statusCode': code,
       'headers': {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': '*'
    },
        'body': json.dumps({
            'success': success,
            'code': code,
            'data': data
        }, default=str)
    }


def lambda_handler(event, context):
    try:
        # Fixed tenant ID as requested
        tenant_id = "Ecommerce00"

        # Determine HTTP Method
        http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')
        resource_path = event.get('requestContext', {}).get('resourcePath') or event.get('path', '')

        # Route uploads to S3 presigned URL generator
        if http_method == 'POST' and 'upload' in resource_path:
            return handle_upload(event)

        if http_method == 'POST':
            return handle_post(event, tenant_id)
        elif http_method == 'PUT':
            return handle_put(event, tenant_id)
        elif http_method == 'DELETE':
            return handle_delete(event, tenant_id)
        elif http_method == 'OPTIONS':
            return make_response(True, 200, {'message': 'CORS preflight successful'})
        else:
            return make_response(False, 405, {'error': f'Method {http_method} not allowed'})

    except Exception as e:
        capture_exception(e)
        print(f"Error: {str(e)}")
        return make_response(False, 500, {'error': f'Internal server error: {str(e)}'})


def handle_upload(event):
    """
    Generates a secure presigned PUT URL to upload product images directly to S3.
    """
    try:
        raw_body = json.loads(event.get('body', '{}'))
        body = raw_body.get('data', raw_body)
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    filename = body.get('filename')
    content_type = body.get('contentType') or body.get('content_type') or 'image/png'

    if not filename:
        return make_response(False, 400, {'error': 'Missing filename parameter'})

    if not BUCKET_NAME:
        return make_response(False, 500, {'error': 'S3 Bucket name not configured in environment'})

    # Generate a unique key for the S3 object
    file_ext = os.path.splitext(filename)[1] or '.png'
    unique_id = str(uuid.uuid4())
    image_key = f"products/{unique_id}{file_ext}"

    try:
        # Generate PUT URL
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': image_key,
                'ContentType': content_type
            },
            ExpiresIn=3600
        )

        image_url = f"s3://{BUCKET_NAME}/{image_key}"

        return make_response(True, 200, {
            'upload_url': presigned_url,
            'image_url': image_url,
            'image_key': image_key
        })
    except Exception as e:
        capture_exception(e)
        print(f"Error generating presigned URL: {str(e)}")
        return make_response(False, 500, {'error': f'Error generating upload URL: {str(e)}'})


def handle_post(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    # Handle standard flat payload or legacy wrapped payload
    data = body.get('data') if isinstance(body.get('data'), dict) else body

    # Extract new e-commerce and legacy properties
    name = data.get('name') or data.get('nombre')
    category = data.get('category') or data.get('tipo') or data.get('type')
    description = data.get('description') or data.get('descripcion', '')
    price_val = data.get('price')
    discount_price_val = data.get('discount_price', 0)
    stock_val = data.get('stock')
    image_url = data.get('image_url')
    status = data.get('status', 'ACTIVE')

    if not name or not category:
        return make_response(False, 400, {'error': 'Missing required fields: name (nombre), category (category/tipo)'})

    try:
        price = float(price_val) if price_val is not None else 0.0
    except (ValueError, TypeError):
        return make_response(False, 400, {'error': 'Price must be a number'})

    try:
        discount_price = float(discount_price_val) if discount_price_val is not None else 0.0
    except (ValueError, TypeError):
        return make_response(False, 400, {'error': 'Discount price must be a number'})

    try:
        stock = int(stock_val) if stock_val is not None else 0
    except (ValueError, TypeError):
        return make_response(False, 400, {'error': 'Stock must be an integer'})

    # Check if a product with the same name already exists in this tenant
    response = table.query(
        KeyConditionExpression=Key('PK').eq(f"TENANT#{tenant_id}") & Key('SK').begins_with("PRODUCT#"),
        FilterExpression=Attr('name').eq(name) | Attr('nombre').eq(name)
    )
    existing_items = response.get('Items', [])

    if existing_items:
        # Sum stock for the existing product
        existing_item = existing_items[0]
        existing_id = existing_item.get('product_id') or existing_item.get('id_producto')
        existing_stock = int(existing_item.get('stock', 0))
        nuevo_stock = existing_stock + stock
        updated_at = datetime.utcnow().isoformat()

        table.update_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"PRODUCT#{existing_id}"
            },
            UpdateExpression="SET stock = :s, updated_at = :u",
            ExpressionAttributeValues={
                ':s': nuevo_stock,
                ':u': updated_at
            }
        )

        return make_response(True, 200, {
            'message': f"El producto '{name}' ya existe. Se ha actualizado el stock.",
            'producto': {
                'id_producto': existing_id,
                'product_id': existing_id,
                'name': name,
                'description': existing_item.get('description') or existing_item.get('descripcion', ''),
                'category': existing_item.get('category') or existing_item.get('tipo', ''),
                'price': float(existing_item.get('price', 0.0)),
                'discount_price': float(existing_item.get('discount_price', 0.0)),
                'stock': nuevo_stock,
                'image_url': existing_item.get('image_url', ''),
                'status': existing_item.get('status', 'ACTIVE')
            }
        })

    # Create new product
    product_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    item = {
        'PK': f"TENANT#{tenant_id}",
        'SK': f"PRODUCT#{product_id}",
        'id_producto': product_id,
        'product_id': product_id,
        'name': name,
        'nombre': name,
        'description': description,
        'descripcion': description,
        'category': category,
        'tipo': category,
        'type': category, # Index range key compatibility
        'price': Decimal(str(price)),
        'discount_price': Decimal(str(discount_price)),
        'stock': stock,
        'image_url': image_url or "",
        'status': status,
        'created_at': created_at,
        'updated_at': created_at
    }

    table.put_item(Item=item)

    print(json.dumps({
        "level": "INFO",
        "module": "manageAsset",
        "action": "create_product",
        "tenant_id": tenant_id,
        "product_id": product_id,
        "status": "success"
    }))

    return make_response(True, 201, {
        'message': 'Product created successfully',
        'producto': {
            'id_producto': product_id,
            'product_id': product_id,
            'name': name,
            'description': description,
            'category': category,
            'price': price,
            'discount_price': discount_price,
            'stock': stock,
            'image_url': image_url or "",
            'status': status
        }
    })


def handle_put(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    # Extract flat payload or legacy wrapped payload
    data = body.get('data') if isinstance(body.get('data'), dict) else body
    id_producto = data.get('product_id') or data.get('id_producto')

    if not id_producto:
        return make_response(False, 400, {'error': 'Missing required field: product_id (id_producto)'})

    # Fetch existing product
    response = table.get_item(
        Key={
            'PK': f"TENANT#{tenant_id}",
            'SK': f"PRODUCT#{id_producto}"
        }
    )
    existing_item = response.get('Item')
    if not existing_item:
        return make_response(False, 404, {'error': 'Producto no encontrado'})

    action = data.get('action')
    current_stock = int(existing_item.get('stock', 0))

    # Action-based stock operations
    if action:
        cant_val = data.get('cantidad') or data.get('stock')
        try:
            cantidad = int(cant_val) if cant_val is not None else 1
        except (ValueError, TypeError):
            return make_response(False, 400, {'error': 'Quantity/Stock must be an integer'})

        if action in ['buy', 'vender', 'sell']:
            if current_stock < cantidad:
                return make_response(False, 400, {
                    'error': f"Stock insuficiente para realizar la venta. Stock actual: {current_stock}"
                })
            nuevo_stock = current_stock - cantidad
            msg = f"Venta realizada. Se descontaron {cantidad} unidades del producto '{existing_item.get('name') or existing_item.get('nombre')}'."
        elif action in ['restock', 'comprar', 'add']:
            nuevo_stock = current_stock + cantidad
            msg = f"Compra/Reabastecimiento registrado. Se sumaron {cantidad} unidades al producto '{existing_item.get('name') or existing_item.get('nombre')}'."
        else:
            return make_response(False, 400, {'error': f"Accion '{action}' no valida"})

        updated_at = datetime.utcnow().isoformat()
        table.update_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"PRODUCT#{id_producto}"
            },
            UpdateExpression="SET stock = :s, updated_at = :u",
            ExpressionAttributeValues={
                ':s': nuevo_stock,
                ':u': updated_at
            }
        )

        return make_response(True, 200, {
            'message': msg,
            'producto': {
                'id_producto': id_producto,
                'product_id': id_producto,
                'name': existing_item.get('name') or existing_item.get('nombre'),
                'description': existing_item.get('description') or existing_item.get('descripcion', ''),
                'category': existing_item.get('category') or existing_item.get('tipo'),
                'price': float(existing_item.get('price', 0.0)),
                'discount_price': float(existing_item.get('discount_price', 0.0)),
                'stock': nuevo_stock,
                'image_url': existing_item.get('image_url', ''),
                'status': existing_item.get('status', 'ACTIVE')
            }
        })

    # Standard fields update
    update_expr = []
    expr_attr_values = {}
    expr_attr_names = {}

    updatable_fields = [
        'name', 'nombre', 
        'description', 'descripcion', 
        'category', 'tipo', 
        'price', 'discount_price', 
        'stock', 'image_url', 'status'
    ]

    for field in updatable_fields:
        if field in data:
            val = data[field]
            if field == 'stock':
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    return make_response(False, 400, {'error': 'Stock must be an integer'})
            elif field in ['price', 'discount_price']:
                try:
                    val = Decimal(str(val))
                except (ValueError, TypeError):
                    return make_response(False, 400, {'error': f'{field} must be a number'})

            # Handle compatibility properties mapping
            if field in ['category', 'tipo']:
                update_expr.append("#category = :category")
                expr_attr_names["#category"] = "category"
                expr_attr_values[":category"] = val
                
                update_expr.append("#tipo = :tipo")
                expr_attr_names["#tipo"] = "tipo"
                expr_attr_values[":tipo"] = val
                
                update_expr.append("#type = :type")
                expr_attr_names["#type"] = "type"
                expr_attr_values[":type"] = val
            elif field in ['name', 'nombre']:
                update_expr.append("#name = :name")
                expr_attr_names["#name"] = "name"
                expr_attr_values[":name"] = val
                
                update_expr.append("#nombre = :nombre")
                expr_attr_names["#nombre"] = "nombre"
                expr_attr_values[":nombre"] = val
            elif field in ['description', 'descripcion']:
                update_expr.append("#description = :description")
                expr_attr_names["#description"] = "description"
                expr_attr_values[":description"] = val
                
                update_expr.append("#descripcion = :descripcion")
                expr_attr_names["#descripcion"] = "descripcion"
                expr_attr_values[":descripcion"] = val
            else:
                update_expr.append(f"#{field} = :{field}")
                expr_attr_names[f"#{field}"] = field
                expr_attr_values[f":{field}"] = val

    if not update_expr:
        return make_response(False, 400, {'error': 'No fields provided to update'})

    updated_at = datetime.utcnow().isoformat()
    update_expr.append("#updated_at = :updated_at")
    expr_attr_names["#updated_at"] = "updated_at"
    expr_attr_values[":updated_at"] = updated_at

    update_expression = "SET " + ", ".join(update_expr)

    try:
        db_res = table.update_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"PRODUCT#{id_producto}"
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ConditionExpression="attribute_exists(PK)",
            ReturnValues="ALL_NEW"
        )
        updated_item = db_res.get('Attributes', {})
        return make_response(True, 200, {
            'message': 'Producto actualizado exitosamente',
            'producto': {
                'id_producto': updated_item.get('product_id') or updated_item.get('id_producto'),
                'product_id': updated_item.get('product_id') or updated_item.get('id_producto'),
                'name': updated_item.get('name') or updated_item.get('nombre'),
                'description': updated_item.get('description') or updated_item.get('descripcion', ''),
                'category': updated_item.get('category') or updated_item.get('tipo'),
                'price': float(updated_item.get('price', 0.0)),
                'discount_price': float(updated_item.get('discount_price', 0.0)),
                'stock': int(updated_item.get('stock', 0)),
                'image_url': updated_item.get('image_url', ''),
                'status': updated_item.get('status', 'ACTIVE')
            }
        })
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return make_response(False, 404, {'error': 'Producto no encontrado'})


def handle_delete(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    data = body.get('data') if isinstance(body.get('data'), dict) else body
    id_producto = data.get('product_id') or data.get('id_producto')

    if not id_producto:
        return make_response(False, 400, {'error': 'Missing required field: product_id (id_producto)'})

    try:
        table.delete_item(
            Key={
                'PK': f"TENANT#{tenant_id}",
                'SK': f"PRODUCT#{id_producto}"
            },
            ConditionExpression="attribute_exists(PK)"
        )
        return make_response(True, 200, {'message': 'Producto eliminado exitosamente'})
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return make_response(False, 404, {'error': 'Producto no encontrado'})
