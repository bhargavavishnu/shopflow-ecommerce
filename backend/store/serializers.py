from rest_framework import serializers
from store.models import Cart, CartItem, Category, Order, OrderItem, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    stock_on_hand = serializers.IntegerField(
        source="inventory.stock_on_hand", read_only=True, default=0
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "slug",
            "description",
            "category",
            "category_name",
            "category_slug",
            "price_cents",
            "stock_on_hand",
            "is_active",
        ]


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    line_total_cents = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ["id", "product", "quantity", "line_total_cents"]

    def get_line_total_cents(self, obj):
        return obj.product.price_cents * obj.quantity


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total_cents = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ["id", "session_key", "items", "total_cents", "updated_at"]

    def get_total_cents(self, obj):
        return sum(item.product.price_cents * item.quantity for item in obj.items.all())


class OrderItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "sku", "product_name", "unit_price_cents", "quantity"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer_name",
            "customer_email",
            "shipping_address",
            "status",
            "total_cents",
            "payment_intent_id",
            "confirmation_sent",
            "created_at",
            "updated_at",
            "items",
        ]
