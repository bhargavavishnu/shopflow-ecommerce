import uuid
import stripe
from django.conf import settings


class PaymentError(Exception):
    """Raised when a payment charge fails or is declined."""


class StripePaymentService:
    """Wrapper around Stripe test-mode PaymentIntents with local simulation fallback."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.STRIPE_SECRET_KEY

    @property
    def is_live_stripe_configured(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("sk_test_simulated")

    def create_and_confirm_payment(
        self,
        *,
        amount_cents: int,
        currency: str = "usd",
        customer_email: str,
        payment_token: str = "tok_visa",
        metadata: dict | None = None,
    ) -> dict:
        if amount_cents <= 0:
            raise PaymentError("Charge amount must be positive")

        if payment_token in {"tok_declined", "pm_card_chargeDeclined"}:
            raise PaymentError("Card was declined in test mode")

        if self.is_live_stripe_configured:
            stripe.api_key = self.api_key
            try:
                intent = stripe.PaymentIntent.create(
                    amount=amount_cents,
                    currency=currency,
                    receipt_email=customer_email,
                    payment_method=payment_token
                    if payment_token.startswith("pm_")
                    else "pm_card_visa",
                    confirm=True,
                    automatic_payment_methods={
                        "enabled": True,
                        "allow_redirects": "never",
                    },
                    metadata=metadata or {},
                )
                return {
                    "id": intent["id"],
                    "status": intent["status"],
                    "mode": "stripe_api",
                }
            except stripe.error.StripeError as exc:
                raise PaymentError(str(exc)) from exc

        return {
            "id": f"pi_test_{uuid.uuid4().hex[:16]}",
            "status": "succeeded",
            "mode": "stripe_test_simulated",
        }
