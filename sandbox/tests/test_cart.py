import pytest
from src.ecommerce.services.cart_service import CartService
from src.ecommerce.models.product import Product


def test_add_to_cart():
    cart = CartService()
    p = Product(id=1, name="Widget", price=9.99, stock=10, category="gadgets")
    cart.add_to_cart(1, p, 2)
    assert len(cart.get_cart(1)) == 1


def test_cart_total():
    cart = CartService()
    p = Product(id=1, name="Widget", price=10.0, stock=10, category="gadgets")
    cart.add_to_cart(1, p, 3)
    try:
        total = cart.cart_total(1)
        assert total == 30.0
    except KeyError:
        pytest.fail("cart_total has a bug with quantity key")


def test_remove_from_cart():
    cart = CartService()
    p1 = Product(id=1, name="A", price=1.0, stock=5, category="test")
    p2 = Product(id=2, name="B", price=2.0, stock=5, category="test")
    cart.add_to_cart(1, p1, 1)
    cart.add_to_cart(1, p2, 1)
    cart.remove_from_cart(1, 1)
    assert len(cart.get_cart(1)) == 1


def test_duplicate_items_in_cart():
    cart = CartService()
    p = Product(id=1, name="Widget", price=10.0, stock=10, category="gadgets")
    cart.add_to_cart(1, p, 1)
    cart.add_to_cart(1, p, 1)
    assert len(cart.get_cart(1)) == 2  # Should ideally be 1 with quantity 2
