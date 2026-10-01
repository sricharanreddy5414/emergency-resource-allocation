# Cancellation policy

Company approval recorded for the current V1 launch documents.

COMPANY APPROVAL = CONFIRMED for the controlled limited beta. This is not legal counsel approval and it is not regulatory approval.

This is the V1 cancellation behavior already implemented in ERAP. It is the launch policy for that behavior. It is not a lawyer-approved legal instrument.

Only an owner can cancel. Cancellation is scheduled for the end of the current period. The subscription stays `ACTIVE` until that period ends, and operational writes stay allowed. When the period ends, the expiry worker sets the subscription to `CANCELLED`. ERAP does not cancel immediately.

A provider cancellation received while the subscription is `PAST_DUE` does not by itself end operational writes. That is the V1 rule. `PAST_DUE` can return to `ACTIVE` only through a verified provider event, or move to `EXPIRED` when that transition is legal.

Company approval of this V1 text is recorded for the controlled limited beta. Legal counsel approval is not claimed.
