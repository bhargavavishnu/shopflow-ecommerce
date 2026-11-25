from concurrent.futures import ThreadPoolExecutor
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TransactionTestCase
import pytest
from rest_framework.test import APIClient
import stripe

from shopflow.celery import app as celery_app
from store.models import Category, Inventory, Order, Product
from store.services.checkout import (
    CheckoutValidationError,
    InsufficientStockError,
    process_checkout,
)
from store.services.payments import PaymentError, StripePaymentService
from store.tasks import audit_low_stock_inventory, send_order_confirmation_email


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def sample_catalog(db):
    cat = Category.objects.create(name="Keyboards", slug="keyboards")
    p1 = Product.objects.create(
        sku="KB-100",
        name="Tactile Mechanical Keyboard Pro",
        slug="tactile-mechanical-keyboard-pro",
        description="Hot-swap QMK mechanical keyboard with aluminum case.",
        category=cat,
        price_cents=18900,
        is_active=True,
    )
    p1.update_search_vector()
    Inventory.objects.create(product=p1, stock_on_hand=15, low_stock_threshold=5)

    p2 = Product.objects.create(
        sku="KB-200",
        name="Silent Linear Compact Keyboard",
        slug="silent-linear-compact-keyboard",
        description="Compact wireless ergonomic switch board.",
        category=cat,
        price_cents=12900,
        is_active=True,
    )
    p2.update_search_vector()
    Inventory.objects.create(product=p2, stock_on_hand=3, low_stock_threshold=5)

    inactive = Product.objects.create(
        sku="KB-OLD",
        name="Discontinued Prototype",
        slug="discontinued-prototype",
        description="Archived item",
        category=cat,
        price_cents=9900,
        is_active=False,
    )
    Inventory.objects.create(product=inactive, stock_on_hand=10)
    return {"category": cat, "p1": p1, "p2": p2, "inactive": inactive}


@pytest.mark.django_db
def test_health_and_catalog_full_text_search(api_client, sample_catalog):
    health = api_client.get("/api/health/")
    assert health.status_code == 200
    assert health.data["postgres"] == "up"
    assert health.data["redis"] is True

    cats = api_client.get("/api/categories/")
    assert cats.status_code == 200
    assert len(cats.data["categories"]) == 1

    filtered = api_client.get(
        "/api/products/?category=keyboards&min_price=10000&max_price=20000&in_stock=1"
    )
    assert filtered.status_code == 200
    assert filtered.data["count"] == 2

    fts = api_client.get("/api/products/?q=mechanical+aluminum")
    assert fts.status_code == 200
    assert fts.data["count"] == 1
    assert fts.data["results"][0]["sku"] == "KB-100"

    detail = api_client.get(f"/api/products/{sample_catalog['p1'].slug}/")
    assert detail.status_code == 200
    assert detail.data["product"]["stock_on_hand"] == 15


@pytest.mark.django_db(transaction=True)
def test_cart_flow_and_api_checkout_happy_path(api_client, sample_catalog):
    prev_eager = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        _run_cart_and_checkout_happy_path(api_client, sample_catalog)
    finally:
        celery_app.conf.task_always_eager = prev_eager


def _run_cart_and_checkout_happy_path(api_client, sample_catalog):
    p1 = sample_catalog["p1"]
    p2 = sample_catalog["p2"]
    headers = {"HTTP_X_SESSION_KEY": "sess-happy"}

    add1 = api_client.post(
        "/api/cart/items/",
        {"product_id": p1.id, "quantity": 2},
        format="json",
        **headers,
    )
    assert add1.status_code == 201
    assert add1.data["cart"]["total_cents"] == 37800

    # Add same product increments quantity to 3
    add2 = api_client.post(
        "/api/cart/items/",
        {"product_id": p1.id, "quantity": 1},
        format="json",
        **headers,
    )
    item_id = add2.data["cart"]["items"][0]["id"]
    assert add2.data["cart"]["items"][0]["quantity"] == 3

    # Add second item then delete it via DELETE /api/cart/items/<id>/
    add_extra = api_client.post(
        "/api/cart/items/",
        {"product_id": p2.id, "quantity": 1},
        format="json",
        **headers,
    )
    extra_id = [i["id"] for i in add_extra.data["cart"]["items"] if i["product"]["id"] == p2.id][0]
    del_extra = api_client.delete(f"/api/cart/items/{extra_id}/", **headers)
    assert del_extra.status_code == 200

    # Patch quantity to 2
    patch = api_client.patch(
        f"/api/cart/items/{item_id}/",
        {"quantity": 2},
        format="json",
        **headers,
    )
    assert patch.status_code == 200
    assert patch.data["cart"]["total_cents"] == 37800

    # Checkout from cart
    checkout = api_client.post(
        "/api/checkout/",
        {
            "customer_name": "Ada Lovelace",
            "customer_email": "ada@example.com",
            "shipping_address": "10 Computing Way",
            "payment_token": "tok_visa",
        },
        format="json",
        **headers,
    )
    assert checkout.status_code == 201
    order_num = checkout.data["order"]["order_number"]
    assert checkout.data["order"]["total_cents"] == 37800
    assert checkout.data["order"]["payment_intent_id"].startswith("pi_test_")

    p1.inventory.refresh_from_db()
    assert p1.inventory.stock_on_hand == 13

    # Cart should be empty after checkout
    cart_after = api_client.get("/api/cart/", **headers)
    assert cart_after.data["cart"]["items"] == []

    # Track order via REST API (Celery eager on_commit updated confirmation_sent=True)
    track = api_client.get(f"/api/orders/{order_num}/")
    assert track.status_code == 200
    assert track.data["order"]["confirmation_sent"] is True
    assert track.data["order"]["status"] == Order.Status.PROCESSING

    listing = api_client.get("/api/orders/?email=ada@example.com")
    assert listing.status_code == 200
    assert len(listing.data["orders"]) == 1


@pytest.mark.django_db
def test_checkout_validation_insufficient_stock_and_declined_card(api_client, sample_catalog, monkeypatch):
    p1 = sample_catalog["p1"]
    p2 = sample_catalog["p2"]
    inactive = sample_catalog["inactive"]

    # Missing fields -> 400
    bad_req = api_client.post(
        "/api/checkout/",
        {"customer_name": "", "items": [{"product_id": p1.id, "quantity": 1}]},
        format="json",
    )
    assert bad_req.status_code == 400

    # Empty cart checkout -> 400
    empty_req = api_client.post(
        "/api/checkout/",
        {
            "customer_name": "Bob",
            "customer_email": "bob@example.com",
            "shipping_address": "22 Main St",
        },
        format="json",
        HTTP_X_SESSION_KEY="sess-empty",
    )
    assert empty_req.status_code == 400

    # Insufficient stock -> 409 and inventory unchanged
    oversell = api_client.post(
        "/api/checkout/",
        {
            "customer_name": "Bob",
            "customer_email": "bob@example.com",
            "shipping_address": "22 Main St",
            "items": [{"product_id": p2.id, "quantity": 99}],
        },
        format="json",
    )
    assert oversell.status_code == 409
    p2.inventory.refresh_from_db()
    assert p2.inventory.stock_on_hand == 3

    # Declined payment -> 402 and atomic rollback leaves stock intact
    declined = api_client.post(
        "/api/checkout/",
        {
            "customer_name": "Bob",
            "customer_email": "bob@example.com",
            "shipping_address": "22 Main St",
            "payment_token": "tok_declined",
            "items": [{"product_id": p2.id, "quantity": 2}],
        },
        format="json",
    )
    assert declined.status_code == 402
    p2.inventory.refresh_from_db()
    assert p2.inventory.stock_on_hand == 3
    assert Order.objects.filter(customer_email="bob@example.com").count() == 0

    with pytest.raises(CheckoutValidationError):
        process_checkout(
            customer_name="Bob",
            customer_email="bob@example.com",
            shipping_address="22 Main St",
            items=[{"product_id": inactive.id, "quantity": 1}],
        )

    with pytest.raises(CheckoutValidationError):
        process_checkout(
            customer_name="Bob",
            customer_email="bob@example.com",
            shipping_address="22 Main St",
            items=[{"product_id": 999999, "quantity": 1}],
        )

    # Verify live Stripe SDK adapter call path using monkeypatched SDK client
    monkeypatch.setattr(
        stripe.PaymentIntent,
        "create",
        lambda **kwargs: {"id": "pi_stripe_sdk_mock", "status": "succeeded"},
    )
    svc = StripePaymentService(api_key="sk_test_real_mocked")
    res = svc.create_and_confirm_payment(
        amount_cents=5000, customer_email="bob@example.com", payment_token="pm_card_visa"
    )
    assert res["id"] == "pi_stripe_sdk_mock"
    assert res["mode"] == "stripe_api"


@pytest.mark.django_db
def test_celery_tasks_and_seed_command(sample_catalog):
    p2 = sample_catalog["p2"]
    order = process_checkout(
        customer_name="Grace Hopper",
        customer_email="grace@example.com",
        shipping_address="1 Compiler Way",
        items=[{"product_id": p2.id, "quantity": 2}],
    )
    res_email = send_order_confirmation_email.run(order.id)
    assert res_email["status"] == Order.Status.PROCESSING
    order.refresh_from_db()
    assert order.confirmation_sent is True

    alerts = audit_low_stock_inventory.run([p2.id])
    assert len(alerts) == 1
    assert alerts[0]["stock_on_hand"] == 1

    with pytest.raises(PaymentError):
        StripePaymentService(api_key="sk_test_simulated").create_and_confirm_payment(
            amount_cents=0, customer_email="x@y.com"
        )

    # Seed management command sanity check
    call_command("seed_products", count=10)
    assert Product.objects.count() >= 10


class ConcurrentCheckoutConcurrencyTests(TransactionTestCase):
    def test_concurrent_checkout_prevents_overselling(self):
        cat = Category.objects.create(name="Limited Drops", slug="limited-drops")
        product = Product.objects.create(
            sku="LTD-001",
            name="Limited Edition Board",
            slug="limited-edition-board",
            description="Only 3 units available",
            category=cat,
            price_cents=25000,
            is_active=True,
        )
        Inventory.objects.create(product=product, stock_on_hand=3, low_stock_threshold=1)

        def attempt_buy(idx: int):
            close_old_connections()
            try:
                order = process_checkout(
                    customer_name=f"Buyer {idx}",
                    customer_email=f"buyer{idx}@example.com",
                    shipping_address=f"{idx} Concurrency Blvd",
                    items=[{"product_id": product.id, "quantity": 1}],
                )
                return ("ok", order.order_number)
            except InsufficientStockError:
                return ("out_of_stock", None)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt_buy, range(8)))

        succeeded = [o for o in outcomes if o[0] == "ok"]
        blocked = [o for o in outcomes if o[0] == "out_of_stock"]

        assert len(succeeded) == 3
        assert len(blocked) == 5

        inv = Inventory.objects.get(product=product)
        assert inv.stock_on_hand == 0
        assert Order.objects.filter(items__product=product).count() == 3
