def allocate_resource(resources, request):
    """
    Find the first available resource that matches
    the requested type and location.
    """

    for resource in resources:

        if (
            resource["type"] == request["resource_type"]
            and resource["location"] == request["location"]
            and resource["available"] is True
        ):
            resource["available"] = False

            return resource["id"]

    return None


def test_successful_allocation():

    resources = [
        {
            "id": "R001",
            "type": "ICU_BED",
            "location": "Bangalore",
            "available": True
        }
    ]

    request = {
        "request_id": "REQ001",
        "resource_type": "ICU_BED",
        "location": "Bangalore"
    }

    result = allocate_resource(resources, request)

    assert result == "R001"
    assert resources[0]["available"] is False


def test_resource_type_mismatch():

    resources = [
        {
            "id": "R001",
            "type": "GENERAL_BED",
            "location": "Bangalore",
            "available": True
        }
    ]

    request = {
        "request_id": "REQ002",
        "resource_type": "ICU_BED",
        "location": "Bangalore"
    }

    result = allocate_resource(resources, request)

    assert result is None
    assert resources[0]["available"] is True


def test_location_mismatch():

    resources = [
        {
            "id": "R001",
            "type": "ICU_BED",
            "location": "Bangalore",
            "available": True
        }
    ]

    request = {
        "request_id": "REQ003",
        "resource_type": "ICU_BED",
        "location": "Chennai"
    }

    result = allocate_resource(resources, request)

    assert result is None
    assert resources[0]["available"] is True


def test_unavailable_resource():

    resources = [
        {
            "id": "R001",
            "type": "ICU_BED",
            "location": "Bangalore",
            "available": False
        }
    ]

    request = {
        "request_id": "REQ004",
        "resource_type": "ICU_BED",
        "location": "Bangalore"
    }

    result = allocate_resource(resources, request)

    assert result is None
    assert resources[0]["available"] is False


def test_multiple_resources():

    resources = [
        {
            "id": "R001",
            "type": "ICU_BED",
            "location": "Bangalore",
            "available": False
        },
        {
            "id": "R002",
            "type": "ICU_BED",
            "location": "Bangalore",
            "available": True
        }
    ]

    request = {
        "request_id": "REQ005",
        "resource_type": "ICU_BED",
        "location": "Bangalore"
    }

    result = allocate_resource(resources, request)

    assert result == "R002"
    assert resources[1]["available"] is False


if __name__ == "__main__":
    print("Running allocation tests...")

    test_successful_allocation()
    test_resource_type_mismatch()
    test_location_mismatch()
    test_unavailable_resource()
    test_multiple_resources()

    print("All allocation tests passed successfully!")