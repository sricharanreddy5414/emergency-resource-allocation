"""Organization billing history. The table is not scanned."""

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .errors import BillingError


EVENTS_INDEX = "OrganizationBillingEventsIndex"
EVENT_LIMIT = 50
PUBLIC_FIELDS = (
    "provider_event_id",
    "provider",
    "event_type",
    "organization_id",
    "provider_payment_id",
    "received_at",
    "processed_at",
    "processing_status",
)


def list_events(events, organization_id):
    try:
        result = events.query(
            IndexName=EVENTS_INDEX,
            KeyConditionExpression=Key("organization_id").eq(organization_id),
            ScanIndexForward=False,
            Limit=EVENT_LIMIT,
        )
    except ClientError as error:
        print("Billing events read failed:", error.response["Error"]["Code"])
        raise BillingError(500, "Unable to read billing events")

    return {
        "organization_id": organization_id,
        "events": [_public_event(item) for item in result.get("Items") or []],
    }


def _public_event(item):
    public = {}

    for field in PUBLIC_FIELDS:
        value = item.get(field)
        public[field] = value if isinstance(value, str) and value.strip() else None

    public["provider_event_id"] = item.get("provider_event_id") or ""
    public["provider"] = item.get("provider") or ""
    public["event_type"] = item.get("event_type") or ""
    public["organization_id"] = item.get("organization_id") or ""
    public["processing_status"] = item.get("processing_status") or ""
    return public
