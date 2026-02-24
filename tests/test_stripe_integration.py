"""TDD tests for stripe_integration.py - Stripe billing for utility clients."""
import pytest
from unittest.mock import patch, MagicMock
import json


@pytest.fixture
def mock_stripe():
    with patch('stripe_integration.stripe') as mock:
        yield mock


class TestCheckoutSession:
    """Test Stripe checkout session creation."""

    def test_creates_session(self, mock_stripe):
        from stripe_integration import create_checkout_session
        mock_stripe.checkout.Session.create.return_value = MagicMock(
            id="cs_test_123", url="https://checkout.stripe.com/test"
        )
        session = create_checkout_session(
            utility_client_id=1,
            tier="starter",
            success_url="https://openleg.ch/utility/dashboard?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://openleg.ch/utility/billing",
        )
        assert session.url == "https://checkout.stripe.com/test"
        mock_stripe.checkout.Session.create.assert_called_once()

    def test_starter_price(self, mock_stripe):
        from stripe_integration import TIER_PRICES
        assert "starter" in TIER_PRICES
        assert "professional" in TIER_PRICES
        assert "enterprise" in TIER_PRICES


class TestWebhook:
    """Test Stripe webhook handling."""

    def test_valid_signature(self, mock_stripe):
        from stripe_integration import handle_webhook
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": "1", "subscription": "sub_123", "customer": "cus_123"}},
        }
        result = handle_webhook(payload=b'test', sig_header="test_sig")
        assert result["status"] == "ok"

    def test_invalid_signature(self, mock_stripe):
        from stripe_integration import handle_webhook
        mock_stripe.Webhook.construct_event.side_effect = Exception("Invalid signature")
        result = handle_webhook(payload=b'bad', sig_header="bad_sig")
        assert result["status"] == "error"


class TestSubscriptionLifecycle:
    """Test subscription create/update/cancel."""

    def test_cancel_subscription(self, mock_stripe):
        from stripe_integration import cancel_subscription
        mock_stripe.Subscription.modify.return_value = MagicMock(
            id="sub_123", cancel_at_period_end=True
        )
        result = cancel_subscription("sub_123")
        assert result.cancel_at_period_end is True

    def test_get_customer_portal(self, mock_stripe):
        from stripe_integration import create_portal_session
        mock_stripe.billing_portal.Session.create.return_value = MagicMock(
            url="https://billing.stripe.com/portal"
        )
        result = create_portal_session("cus_123", "https://openleg.ch/utility/dashboard")
        assert "billing.stripe.com" in result.url
