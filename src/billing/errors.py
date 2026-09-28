"""Billing domain errors. API handlers can map these later."""


class BillingError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
