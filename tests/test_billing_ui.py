"""Static checks for the billing workspace. The browser does not invent prices."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")


def test_billing_navigation_and_page_exist():
    assert 'id="billingNavItem"' in HTML
    assert 'data-section="billing"' in HTML
    assert 'id="billing"' in HTML
    assert "billing:" in APP
    assert "function loadBilling()" in APP
    assert "function canViewBilling()" in APP
    assert "function canManageBilling()" in APP


def test_billing_uses_the_real_routes_and_not_the_webhook():
    assert "/dev/billing" in APP
    assert 'billingRequest("/plans"' in APP
    assert 'billingRequest("/events"' in APP
    assert 'billingRequest("/checkout"' in APP
    assert 'billingRequest("/cancel"' in APP
    assert "/billing/webhook" not in APP
    assert "razorpay" not in APP.lower()
    assert "webhook_secret" not in APP
    assert "key_secret" not in APP


def test_checkout_and_cancel_do_not_send_client_authority():
    checkout = APP.split("async function startBillingCheckout", 1)[1].split("async function confirmBillingCancellation", 1)[0]
    cancel = APP.split("async function confirmBillingCancellation", 1)[1].split("function renderBilling", 1)[0]
    assert "plan_id: planId" in checkout
    assert "billingSelector()" in checkout
    assert "user_sub" not in checkout
    assert "subscription_status" not in checkout
    assert "provider_subscription_id" not in checkout
    assert "billingSelector()" in cancel
    assert "subscription_status" not in cancel
    assert "cancelled_at" not in cancel
    assert "purchasable" in checkout


def test_states_and_non_purchasable_plans_are_represented():
    for label in (
        "Free trial",
        "Payment required",
        "Subscription expired",
        "Legacy access",
        "Cancellation scheduled",
        "Not currently available",
        "No billing events yet.",
        "BILLING_REQUIRED",
        "Subscription required for this operation.",
    ):
        assert label in APP
    assert "amount_minor" in APP
    assert "₹" not in APP
    assert "$" not in HTML
    assert "free forever" not in APP.lower()
    assert "formatPlanAmount" in APP
    assert "Your organization is currently read-only." in APP


def test_billing_layout_can_stack_on_narrow_screens():
    assert ".billing-plan" in STYLE
    assert ".billing-facts div" in STYLE
    narrow = STYLE.split("@media (max-width: 780px)", 1)[1]
    assert ".billing-plan" in narrow
    assert "overflow-wrap: anywhere" in STYLE
