"""Stripe billing integration for OpenLEG utility clients."""
import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Price IDs per tier (set these after creating products in Stripe dashboard)
TIER_PRICES = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", "price_starter_placeholder"),
    "professional": os.environ.get("STRIPE_PRICE_PROFESSIONAL", "price_pro_placeholder"),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", "price_ent_placeholder"),
}


def create_checkout_session(utility_client_id, tier, success_url, cancel_url):
    """Create Stripe checkout session for a utility client.

    Args:
        utility_client_id: Internal client ID (stored as client_reference_id)
        tier: starter, professional, or enterprise
        success_url: Redirect URL on success
        cancel_url: Redirect URL on cancel

    Returns:
        Stripe Checkout Session object
    """
    price_id = TIER_PRICES.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {tier}")

    return stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(utility_client_id),
        success_url=success_url,
        cancel_url=cancel_url,
    )


def handle_webhook(payload, sig_header):
    """Process Stripe webhook event.

    Returns:
        dict with status and optional message
    """
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        client_id = data.get("client_reference_id")
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")
        _activate_subscription(client_id, subscription_id, customer_id)

    elif event_type == "customer.subscription.deleted":
        subscription_id = data.get("id")
        _deactivate_subscription(subscription_id)

    elif event_type == "invoice.payment_failed":
        subscription_id = data.get("subscription")
        _flag_payment_failed(subscription_id)

    return {"status": "ok", "event_type": event_type}


def cancel_subscription(subscription_id):
    """Cancel subscription at period end."""
    return stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)


def create_portal_session(customer_id, return_url):
    """Create Stripe customer portal session."""
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )


def _activate_subscription(client_id, subscription_id, customer_id):
    """Update utility client with Stripe IDs. Placeholder for DB integration."""
    try:
        import database
        database.update_utility_client_stripe(
            int(client_id), subscription_id, customer_id, status="active"
        )
    except Exception:
        pass


def _deactivate_subscription(subscription_id):
    """Deactivate utility client by subscription ID."""
    try:
        import database
        database.deactivate_utility_by_subscription(subscription_id)
    except Exception:
        pass


def _flag_payment_failed(subscription_id):
    """Flag payment failure for a subscription."""
    try:
        import database
        database.flag_utility_payment_failed(subscription_id)
    except Exception:
        pass
