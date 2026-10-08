"""Allowed operational state changes."""

REQUEST_TRANSITIONS = {
    "PENDING": {"ALLOCATED", "CANCELLED"},
    "ALLOCATED": {"RELEASED"},
    "RELEASED": set(),
    "CANCELLED": set(),
}

ALLOCATION_TRANSITIONS = {
    "ALLOCATED": {"RELEASED"},
    "RELEASED": set(),
}


def _normalized(status):
    return str(status or "").upper()


def can_transition(current, target, transitions):
    return target in transitions.get(_normalized(current), set())


def can_allocate_request(status):
    return can_transition(status, "ALLOCATED", REQUEST_TRANSITIONS)


def can_cancel_request(status):
    return can_transition(status, "CANCELLED", REQUEST_TRANSITIONS)


def can_release_request(status):
    return can_transition(status, "RELEASED", REQUEST_TRANSITIONS)


def can_release_allocation(status):
    return can_transition(status, "RELEASED", ALLOCATION_TRANSITIONS)


def can_allocate_resource(available):
    if isinstance(available, str):
        return available.lower() == "true"

    return available is True


def can_release_resource(available):
    return not can_allocate_resource(available)
