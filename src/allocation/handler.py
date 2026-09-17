def allocate_resources(resources, requests):
    """
    Allocate available resources to emergency requests
    using priority-based matching.
    """

    # Priority 1 = highest priority
    requests.sort(key=lambda request: request["priority"])

    allocations = []

    for request in requests:

        for resource in resources:

            if (
                resource["type"] == request["resource_type"]
                and resource["location"] == request["location"]
                and resource["available"] is True
            ):

                resource["available"] = False

                allocation = {
                    "request_id": request["request_id"],
                    "resource_id": resource["id"],
                    "priority": request["priority"],
                    "status": "ALLOCATED"
                }

                allocations.append(allocation)

                break

    return allocations


def print_allocation_results(allocations, resources):
    """
    Display allocation results in a readable format.
    """

    print("===== ALLOCATION RESULTS =====")

    for allocation in allocations:
        print(
            "Request",
            allocation["request_id"],
            "with priority",
            allocation["priority"],
            "allocated resource",
            allocation["resource_id"]
        )

    print("\n===== FINAL RESOURCE STATUS =====")

    for resource in resources:
        print(
            resource["id"],
            "-> Available:",
            resource["available"]
        )


if __name__ == "__main__":

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

    allocations = allocate_resources(resources, requests)

    print_allocation_results(allocations, resources)