import pytest
from src.ecommerce.models.product import Product


def test_product_creation():
    p = Product(id=1, name="Test Product", price=10.0, stock=5, category="test")
    assert p.name == "Test Product"
    assert p.is_in_stock(1)


def test_product_discount():
    p = Product(id=2, name="Discounted", price=100.0, stock=10, category="test")
    result = p.apply_discount(150)
    assert result < 0  # This reveals the bug


def test_product_out_of_stock():
    p = Product(id=3, name="Empty", price=5.0, stock=0, category="test")
    assert p.is_in_stock(0)  # Should this be True?
