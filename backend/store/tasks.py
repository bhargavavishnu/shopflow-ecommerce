import json
from celery import shared_task
import redis
from django.conf import settings

from store.models import Inventory, Order


def _get_redis():
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


@shared_task(name="store.tasks.send_order_confirmation_email")
def send_order_confirmation_email(order_id: int) -> dict:
    """Mark order confirmation as dispatched and record notification in Redis."""
    order = Order.objects.get(pk=order_id)
    if not order.confirmation_sent:
        order.confirmation_sent = True
        if order.status == Order.Status.PAID:
            order.status = Order.Status.PROCESSING
        order.save(update_fields=["confirmation_sent", "status", "updated_at"])

    payload = {
        "order_number": order.order_number,
        "recipient": order.customer_email,
        "total_cents": order.total_cents,
        "status": order.status,
    }
    try:
        r = _get_redis()
        r.lpush("shopflow:confirmation_emails", json.dumps(payload))
        r.ltrim("shopflow:confirmation_emails", 0, 199)
    except Exception:
        pass
    return payload


@shared_task(name="store.tasks.audit_low_stock_inventory")
def audit_low_stock_inventory(product_ids: list[int]) -> list[dict]:
    """Identify products whose remaining stock dropped at or below threshold."""
    alerts = []
    low_items = Inventory.objects.select_related("product").filter(
        product_id__in=product_ids
    )
    for inv in low_items:
        if inv.stock_on_hand <= inv.low_stock_threshold:
            alerts.append(
                {
                    "product_id": inv.product_id,
                    "sku": inv.product.sku,
                    "stock_on_hand": inv.stock_on_hand,
                    "threshold": inv.low_stock_threshold,
                }
            )

    if alerts:
        try:
            r = _get_redis()
            for alert in alerts:
                r.lpush("shopflow:low_stock_alerts", json.dumps(alert))
            r.ltrim("shopflow:low_stock_alerts", 0, 199)
        except Exception:
            pass
    return alerts
