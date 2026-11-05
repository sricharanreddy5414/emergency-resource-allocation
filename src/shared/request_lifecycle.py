"""Emergency request review and fulfillment helpers.

Operational Status stays PENDING until allocation, cancellation, or rejection.
Review is a separate persisted decision so legacy PENDING rows without
review_status remain allocatable.
"""

from datetime import datetime, timezone

REVIEW_PENDING = "PENDING_REVIEW"
REVIEW_APPROVED = "APPROVED"
REVIEW_REJECTED = "REJECTED"
MAX_EVENTS = 20
RECEIPT_COMPLETE = {"RECEIVED", "HANDOVER_COMPLETED"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def review_status(item):
    return str((item or {}).get("review_status") or "").upper()


def allocation_review_allowed(item):
    """Legacy rows with no review_status stay eligible. New rows must be approved."""
    status = review_status(item)
    return status in {"", REVIEW_APPROVED}


def normalize_description(value):
    text = str(value or "").strip()
    return text[:500]


def append_workflow_event(item, event_type, actor_sub="", detail=""):
    events = list((item or {}).get("workflow_events") or [])
    events.append(
        {
            "event": str(event_type or "").upper()[:40],
            "at": utc_now(),
            "detail": str(detail or "")[:200],
        }
    )
    return events[-MAX_EVENTS:]


def quantity_summary(request, allocation=None, deliveries=None):
    requested = request.get("quantity_requested")
    try:
        requested_n = int(requested) if requested is not None else None
    except (TypeError, ValueError):
        requested_n = None
    allocated_n = None
    if allocation and allocation.get("quantity") is not None:
        try:
            allocated_n = int(allocation.get("quantity"))
        except (TypeError, ValueError):
            allocated_n = None
    delivered_n = None
    received_n = None
    for row in deliveries or []:
        try:
            if row.get("quantity_dispatched") is not None:
                delivered_n = int(row.get("quantity_dispatched"))
        except (TypeError, ValueError):
            pass
        try:
            if row.get("quantity_received") is not None:
                received_n = int(row.get("quantity_received"))
        except (TypeError, ValueError):
            pass
    remaining = None
    if requested_n is not None:
        used = received_n if received_n is not None else allocated_n
        remaining = requested_n - used if used is not None else requested_n
    return {
        "quantity_requested": requested_n,
        "quantity_allocated": allocated_n,
        "quantity_delivered": delivered_n,
        "quantity_received": received_n,
        "quantity_remaining": remaining,
        "quantity_unit": request.get("quantity_unit") or "",
    }


def receipt_allows_completion(deliveries):
    rows = list(deliveries or [])
    if not rows:
        return False
    return all(str(row.get("status") or "").upper() in RECEIPT_COMPLETE for row in rows)
