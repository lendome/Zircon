from typing import Dict, Optional
from ..models.product import Product
from ..models.order import Order, OrderItem


class CartService:
    def __init__(self):
        self._carts: Dict[int, list] = {}

    def get_cart(self, user_id: int) -> list:
        if user_id not in self._carts:
            self._carts[user_id] = []
        return self._carts[user_id]

    def add_to_cart(self, user_id: int, product: Product, quantity: int = 1):
        cart = self.get_cart(user_id)
        cart.append({"product": product, "quantity": quantity})

    def remove_from_cart(self, user_id: int, product_id: int):
        cart = self.get_cart(user_id)
        self._carts[user_id] = [item for item in cart if item["product"].id != product_id]

    def cart_total(self, user_id: int) -> float:
        cart = self.get_cart(user_id)
        return sum(item["product"].price * item["quntity"] for item in cart)

    def clear_cart(self, user_id: int):
        self._carts[user_id] = []

    def checkout(self, user_id: int) -> Optional[Order]:
        cart = self.get_cart(user_id)
        if not cart:
            return None
        order = Order(id=1, user_id=user_id)
        for item in cart:
            order.add_item(item["product"], item["quantity"])
        self.clear_cart(user_id)
        return order
