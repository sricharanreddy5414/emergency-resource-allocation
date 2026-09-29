"""Resource Exchange request and offer state. Transitions deferred to later phases."""

REQUEST_STATUSES = frozenset(
    {
        "OPEN",
        "ACCEPTED",
        "TRANSFER_PENDING",
        "COMPLETED",
        "CANCELLED",
        "EXPIRED",
    }
)

OFFER_STATUSES = frozenset(
    {
        "OPEN",
        "ACCEPTED",
        "REJECTED",
        "WITHDRAWN",
        "SUPERSEDED",
        "EXPIRED",
        "CANCELLED",
    }
)

# Hold representation for Resource Exchange (Phase 5D/5E).
ALLOCATION_TYPE_EXCHANGE = "EXCHANGE"
EXCHANGE_ALLOCATION_STATUS_OPEN = "OPEN"
EXCHANGE_ALLOCATION_STATUS_RELEASED = "RELEASED"


class ExchangeStateError(ValueError):
    """Invalid exchange request or offer state."""


def normalize_request_status(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ExchangeStateError("Exchange request status is invalid")

    status = str(value).strip().upper()

    if status not in REQUEST_STATUSES:
        raise ExchangeStateError("Exchange request status is invalid")

    return status


def normalize_offer_status(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ExchangeStateError("Exchange offer status is invalid")

    status = str(value).strip().upper()

    if status not in OFFER_STATUSES:
        raise ExchangeStateError("Exchange offer status is invalid")

    return status
