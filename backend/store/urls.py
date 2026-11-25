from django.urls import path
from store import views

urlpatterns = [
    path("health/", views.health_check, name="health-check"),
    path("categories/", views.category_list, name="category-list"),
    path("products/", views.product_list, name="product-list"),
    path("products/<slug:slug>/", views.product_detail, name="product-detail"),
    path("cart/", views.cart_detail, name="cart-detail"),
    path("cart/items/", views.cart_add_item, name="cart-add-item"),
    path("cart/items/<int:item_id>/", views.cart_update_item, name="cart-update-item"),
    path("checkout/", views.checkout_view, name="checkout"),
    path("orders/", views.order_list, name="order-list"),
    path("orders/<str:order_number>/", views.order_detail, name="order-detail"),
]
