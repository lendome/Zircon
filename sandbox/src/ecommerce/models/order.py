from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from .product import Product


@dataclass
class OrderItem:
    product: Product
    quantity: int
    unit_price: float

    def total_price(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Order:
    id: int
    user_id: int
    items: List[OrderItem] = field(default_factory=list)
    status: str = "pending"
    created_at: Optional[datetime] = None
    shipping_address: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    def total_amount(self) -> float:
        return sum(item.total_price() for item in self.items)

    def add_item(self, product: Product, quantity: int):
        item = OrderItem(product=product, quantity=quantity, unit_price=product.price)
        self.items.append(item)

    def cancel(self):
        self.status = "cancelled"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status,
            "total": self.total_amount(),
            "item_count": len(self.items),
        }
