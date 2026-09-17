def lambda_handler(event, context):
    print("Emergency Resource Allocation Platform started")

    return {
        "statusCode": 200,
        "body": "Platform is working"
    }

lambda_handler({}, {})