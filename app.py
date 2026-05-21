import json
import os
import uuid
import boto3
from datetime import datetime
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

dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME')
table = dynamodb.Table(TABLE_NAME)


def capture_exception(e):
    if SENTRY_ENABLED:
        sentry_sdk.capture_exception(e)


def make_response(success, code, data):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'success': success,
            'code': code,
            'data': data
        }, default=str)
    }


def lambda_handler(event, context):
    try:
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        if not claims:
            claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
        tenant_id = claims.get('custom:tenant_id') or claims.get('tenant_id')

        if not tenant_id:
            return make_response(False, 403, {'error': 'Unauthorized: tenant_id not found in token'})

        http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')

        if http_method == 'POST':
            return handle_post(event, tenant_id)
        elif http_method == 'PUT':
            return handle_put(event, tenant_id)
        elif http_method == 'DELETE':
            return handle_delete(event, tenant_id)
        else:
            return make_response(False, 405, {'error': f'Method {http_method} not allowed'})

    except Exception as e:
        capture_exception(e)
        print(f"Error: {str(e)}")
        return make_response(False, 500, {'error': 'Internal server error'})


def handle_post(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    data = body.get('data', {})
    nombre = data.get('nombre')
    tipo = data.get('tipo') or data.get('type')
    descripcion = data.get('descripcion', '')
    stock_val = data.get('stock')
    fecha_de_registro = data.get('fecha_de_registro')

    if not nombre or not tipo:
        return make_response(False, 400, {'error': 'Missing required fields: nombre, tipo'})

    try:
        stock = int(stock_val) if stock_val is not None else 0
    except (ValueError, TypeError):
        return make_response(False, 400, {'error': 'Stock must be a number'})

    # Check if a product with the same name already exists in this tenant
    response = table.query(
        KeyConditionExpression=Key('PK').eq(f"TENANT#{tenant_id}") & Key('SK').begins_with("PRODUCT#"),
        FilterExpression=Attr('nombre').eq(nombre)
    )
    existing_items = response.get('Items', [])

    if existing_items:
        # Product already exists — sum the stock
        existing_item = existing_items[0]
        existing_id = existing_item.get('id_producto')
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
            'message': f"El producto '{nombre}' ya existe. Se ha actualizado el stock.",
            'producto': {
                'id_producto': existing_id,
                'nombre': nombre,
                'descripcion': existing_item.get('descripcion', ''),
                'tipo': existing_item.get('tipo') or existing_item.get('type', ''),
                'fecha_de_registro': existing_item.get('fecha_de_registro', ''),
                'stock': nuevo_stock
            }
        })

    # New product
    product_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    reg_date = fecha_de_registro if fecha_de_registro else created_at

    item = {
        'PK': f"TENANT#{tenant_id}",
        'SK': f"PRODUCT#{product_id}",
        'id_producto': product_id,
        'nombre': nombre,
        'descripcion': descripcion,
        'tipo': tipo,
        'type': tipo,  # GSI_Type compatibility
        'fecha_de_registro': reg_date,
        'stock': stock,
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
            'nombre': nombre,
            'descripcion': descripcion,
            'tipo': tipo,
            'fecha_de_registro': reg_date,
            'stock': stock
        }
    })


def handle_put(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    data = body.get('data', {})
    id_producto = data.get('id_producto')

    if not id_producto:
        return make_response(False, 400, {'error': 'Missing required field: id_producto'})

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

    if action:
        cant_val = data.get('cantidad') or data.get('stock')
        try:
            cantidad = int(cant_val) if cant_val is not None else 1
        except (ValueError, TypeError):
            return make_response(False, 400, {'error': 'Quantity/Stock must be a number'})

        if action in ['buy', 'vender', 'sell']:
            if current_stock < cantidad:
                return make_response(False, 400, {
                    'error': f"Stock insuficiente para realizar la venta. Stock actual: {current_stock}"
                })
            nuevo_stock = current_stock - cantidad
            msg = f"Venta realizada. Se descontaron {cantidad} unidades del producto '{existing_item.get('nombre')}'."
        elif action in ['restock', 'comprar', 'add']:
            nuevo_stock = current_stock + cantidad
            msg = f"Compra/Reabastecimiento registrado. Se sumaron {cantidad} unidades al producto '{existing_item.get('nombre')}'."
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
                'nombre': existing_item.get('nombre'),
                'descripcion': existing_item.get('descripcion', ''),
                'tipo': existing_item.get('tipo') or existing_item.get('type'),
                'fecha_de_registro': existing_item.get('fecha_de_registro'),
                'stock': nuevo_stock
            }
        })

    # Standard field update
    update_expr = []
    expr_attr_values = {}
    expr_attr_names = {}

    updatable_fields = ['nombre', 'descripcion', 'tipo', 'stock', 'fecha_de_registro']
    for field in updatable_fields:
        if field in data:
            val = data[field]
            if field == 'stock':
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    return make_response(False, 400, {'error': 'Stock must be a number'})

            if field == 'tipo':
                update_expr.append("#tipo = :tipo")
                expr_attr_names["#tipo"] = "tipo"
                expr_attr_values[":tipo"] = val
                update_expr.append("#type = :type")
                expr_attr_names["#type"] = "type"
                expr_attr_values[":type"] = val
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
                'id_producto': updated_item.get('id_producto'),
                'nombre': updated_item.get('nombre'),
                'descripcion': updated_item.get('descripcion', ''),
                'tipo': updated_item.get('tipo') or updated_item.get('type'),
                'fecha_de_registro': updated_item.get('fecha_de_registro'),
                'stock': int(updated_item.get('stock', 0))
            }
        })
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return make_response(False, 404, {'error': 'Producto no encontrado'})


def handle_delete(event, tenant_id):
    try:
        body = json.loads(event.get('body', '{}'))
    except Exception:
        return make_response(False, 400, {'error': 'Invalid JSON body'})

    data = body.get('data', {})
    id_producto = data.get('id_producto')

    if not id_producto:
        return make_response(False, 400, {'error': 'Missing required field: id_producto'})

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
