resources = [
    {
        "id": "R001",
        "type": "ICU_BED",
        "location": "Bangalore",
        "available": True
    },
    {
        "id": "R002",
        "type": "ICU_BED",
        "location": "Bangalore",
        "available": True
    },
    {
        "id": "R003",
        "type": "GENERAL_BED",
        "location": "Bangalore",
        "available": True
    }
]

requests = [
    {
        "request_id": "REQ001",
        "resource_type": "ICU_BED",
        "location": "Bangalore",
        "priority": 2
    },
    {
        "request_id": "REQ002",
        "resource_type": "ICU_BED",
        "location": "Bangalore",
        "priority": 1
    },
    {
        "request_id": "REQ003",
        "resource_type": "GENERAL_BED",
        "location": "Bangalore",
        "priority": 3
    }
]

# Sort requests by priority.
# Priority 1 = highest priority.
requests.sort(key=lambda request: request["priority"])

for request in requests:
    allocated = False

    for resource in resources:
        if (
            resource["type"] == request["resource_type"]
            and resource["location"] == request["location"]
            and resource["available"] == True
        ):
            print(
                "Request",
                request["request_id"],
                "with priority",
                request["priority"],
                "matched with",
                resource["id"]
            )

            resource["available"] = False
            allocated = True

            print(
                "Resource allocated successfully:",
                resource["id"]
            )

            break

    if not allocated:
        print(
            "Allocation failed for",
            request["request_id"],
            "- No suitable resource available"
        )

print("\nFinal resource status:")

for resource in resources:
    print(
        resource["id"],
        "-> Available:",
        resource["available"]
    )