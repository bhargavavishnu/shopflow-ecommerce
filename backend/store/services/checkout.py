from collections import defaultdict
from django.db import transaction
from django.db.models import F

from store.models import Cart, Inventory, Order, OrderItem
from store.services.payments import PaymentError, StripePaymentService
from store.tasks import audit_low_stock_inventory, send_order_confirmation_email


class CheckoutValidationError(Exception):
    """Raised when checkout input is invalid or incomplete."""


class InsufficientStockError(Exception):
    """Raised when requested quantity exceeds available inventory."""


def _dispatch_post_checkout_tasks(order_id: int, product_ids: list[int]) -> None:
    """Enqueue Celery background jobs via Redis after transaction commit."""
    try:
        send_order_confirmation_email.delay(order_id)
        audit_low_stock_inventory.delay(product_ids)
    except Exception:
        # Broker outage should never break an already-committed paid order;
        # production alerts/retries would handle broker recovery.
        pass


def process_checkout(
    *,
    customer_name: str,
    customer_email: str,
    shipping_address: str,
    items: list[dict],
    payment_token: str = "tok_visa",
    cart: Cart | None = None,
    payment_service: StripePaymentService | None = None,
) -> Order:
    """
    Atomically validate inventory using row-level `SELECT FOR UPDATE`, charge via
    Stripe test-mode adapter, deduct stock, create Order & OrderItems, clear cart,
    and schedule asynchronous Celery confirmation/inventory tasks on commit.
    """
    if not customer_name.strip() or not customer_email.strip() or not shipping_address.strip():
        raise CheckoutValidationError(
            "customer_name, customer_email, and shipping_address are required"
        )

    if not items:
        raise CheckoutValidationError("Cannot checkout an empty order")

    requested_by_product: dict[int, int] = defaultdict(int)
    for entry in items:
        pid = int(entry["product_id"])
        qty = int(entry["quantity"])
        if qty <= 0:
            raise CheckoutValidationError("Item quantity must be at least 1")
        requested_by_product[pid] += qty

    sorted_product_ids = sorted(requested_by_product.keys())
    svc = payment_service or StripePaymentService()

    with transaction.atomic():
        # Deterministic lock ordering prevents deadlocks under concurrent checkouts.
        inventories = list(
            Inventory.objects.select_for_update()
            .select_related("product")
            .filter(product_id__in=sorted_product_ids)
            .order_by("product_id")
        )
        inv_map = {inv.product_id: inv for inv in inventories}

        missing = [pid for pid in sorted_product_ids if pid not in inv_map]
        if missing:
            raise CheckoutValidationError(f"Unknown product id(s): {missing}")

        total_cents = 0
        for pid in sorted_product_ids:
            inv = inv_map[pid]
            qty = requested_by_product[pid]
            if not inv.product.is_active:
                raise CheckoutValidationError(
                    f"Product {inv.product.sku} is no longer active"
                )
            if inv.stock_on_hand < qty:
                raise InsufficientStockError(
                    f"Insufficient stock for {inv.product.sku}: requested {qty}, available {inv.stock_on_hand}"
                )
            total_cents += inv.product.price_cents * qty

        charge = svc.create_and_confirm_payment(
            amount_cents=total_cents,
            customer_email=customer_email.strip(),
            payment_token=payment_token,
            metadata={"product_ids": ",".join(map(str, sorted_product_ids))},
        )
        if charge.get("status") != "succeeded":
            raise PaymentError(f"Unexpected payment status: {charge.get('status')}")

        order = Order.objects.create(
            customer_name=customer_name.strip(),
            customer_email=customer_email.strip(),
            shipping_address=shipping_address.strip(),
            status=Order.Status.PAID,
            total_cents=total_cents,
            payment_intent_id=charge["id"],
        )

        order_items = []
        for pid in sorted_product_ids:
            inv = inv_map[pid]
            qty = requested_by_product[pid]
            Inventory.objects.filter(pk=inv.pk).update(
                stock_on_hand=F("stock_on_hand") - qty
            )
            order_items.append(
                OrderItem(
                    order=order,
                    product=inv.product,
                    unit_price_cents=inv.product.price_cents,
                    quantity=qty,
                )
            )
        OrderItem.objects.bulk_create(order_items)

        if cart is not None:
            cart.items.all().delete()

        order_id = order.id
        transaction.on_commit(
            lambda: _dispatch_post_checkout_tasks(order_id, sorted_product_ids)
        )

    order.refresh_from_db()
    return order
