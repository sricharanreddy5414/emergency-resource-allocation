def lambda_handler(event, context):
    print("Emergency request received")

    return {
        "statusCode": 200,
        "body": "Emergency request accepted"
    }

lambda_handler({}, {})