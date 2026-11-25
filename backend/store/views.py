from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connection
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.conf import settings
import redis
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from store.models import Cart, CartItem, Category, Order, Product
from store.serializers import (
    CartSerializer,
    CategorySerializer,
    OrderSerializer,
    ProductSerializer,
)
from store.services.checkout import (
    CheckoutValidationError,
    InsufficientStockError,
    process_checkout,
)
from store.services.payments import PaymentError


def _get_session_key(request) -> str:
    return (
        request.headers.get("X-Session-Key")
        or request.query_params.get("session_key")
        or request.data.get("session_key")
        or "default-storefront-session"
    )


@api_view(["GET"])
def health_check(_request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    pong = r.ping()
    return Response({"status": "ok", "postgres": "up", "redis": pong})


@api_view(["GET"])
def category_list(_request):
    categories = Category.objects.all()
    return Response({"categories": CategorySerializer(categories, many=True).data})


@api_view(["GET"])
def product_list(request):
    qs = Product.objects.select_related("category", "inventory").filter(is_active=True)

    category_slug = request.query_params.get("category")
    if category_slug:
        qs = qs.filter(category__slug=category_slug)

    min_price = request.query_params.get("min_price")
    if min_price:
        qs = qs.filter(price_cents__gte=int(min_price))

    max_price = request.query_params.get("max_price")
    if max_price:
        qs = qs.filter(price_cents__lte=int(max_price))

    if request.query_params.get("in_stock") == "1":
        qs = qs.filter(inventory__stock_on_hand__gt=0)

    q = (request.query_params.get("q") or "").strip()
    if q:
        search_query = SearchQuery(q, config="english", search_type="websearch")
        qs = (
            qs.filter(search_vector=search_query)
            .annotate(rank=SearchRank(F("search_vector"), search_query))
            .order_by("-rank", "id")
        )
    else:
        qs = qs.order_by("id")

    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(qs, request)
    serializer = ProductSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(["GET"])
def product_detail(_request, slug: str):
    product = get_object_or_404(
        Product.objects.select_related("category", "inventory"), slug=slug, is_active=True
    )
    return Response({"product": ProductSerializer(product).data})


@api_view(["GET"])
def cart_detail(request):
    session_key = _get_session_key(request)
    cart, _ = Cart.objects.get_or_create(session_key=session_key)
    cart = Cart.objects.prefetch_related("items__product__category", "items__product__inventory").get(
        pk=cart.pk
    )
    return Response({"cart": CartSerializer(cart).data})


@api_view(["POST"])
def cart_add_item(request):
    session_key = _get_session_key(request)
    product_id = request.data.get("product_id")
    quantity = int(request.data.get("quantity", 1))
    if not product_id or quantity <= 0:
        return Response(
            {"error": "Valid product_id and quantity >= 1 are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    product = get_object_or_404(
        Product.objects.select_related("inventory"), pk=product_id, is_active=True
    )
    cart, _ = Cart.objects.get_or_create(session_key=session_key)
    item, created = CartItem.objects.get_or_create(
        cart=cart, product=product, defaults={"quantity": quantity}
    )
    if not created:
        item.quantity += quantity
        item.save(update_fields=["quantity"])

    cart = Cart.objects.prefetch_related("items__product__category", "items__product__inventory").get(
        pk=cart.pk
    )
    return Response({"cart": CartSerializer(cart).data}, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
def cart_update_item(request, item_id: int):
    session_key = _get_session_key(request)
    cart = get_object_or_404(Cart, session_key=session_key)
    item = get_object_or_404(CartItem, pk=item_id, cart=cart)

    if request.method == "DELETE":
        item.delete()
    else:
        quantity = int(request.data.get("quantity", 0))
        if quantity <= 0:
            item.delete()
        else:
            item.quantity = quantity
            item.save(update_fields=["quantity"])

    cart = Cart.objects.prefetch_related("items__product__category", "items__product__inventory").get(
        pk=cart.pk
    )
    return Response({"cart": CartSerializer(cart).data})


@api_view(["POST"])
def checkout_view(request):
    session_key = _get_session_key(request)
    cart = None
    items = request.data.get("items")

    if items is None:
        cart = (
            Cart.objects.prefetch_related("items")
            .filter(session_key=session_key)
            .first()
        )
        items = (
            [{"product_id": i.product_id, "quantity": i.quantity} for i in cart.items.all()]
            if cart
            else []
        )

    try:
        order = process_checkout(
            customer_name=request.data.get("customer_name", ""),
            customer_email=request.data.get("customer_email", ""),
            shipping_address=request.data.get("shipping_address", ""),
            items=items,
            payment_token=request.data.get("payment_token", "tok_visa"),
            cart=cart,
        )
    except CheckoutValidationError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except InsufficientStockError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
    except PaymentError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_402_PAYMENT_REQUIRED)

    return Response({"order": OrderSerializer(order).data}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def order_list(request):
    qs = Order.objects.prefetch_related("items__product").all()
    email = (request.query_params.get("email") or "").strip()
    if email:
        qs = qs.filter(customer_email__iexact=email)
    return Response({"orders": OrderSerializer(qs[:50], many=True).data})


@api_view(["GET"])
def order_detail(_request, order_number: str):
    order = get_object_or_404(
        Order.objects.prefetch_related("items__product"), order_number=order_number
    )
    return Response({"order": OrderSerializer(order).data})
