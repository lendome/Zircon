from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class Product:
    id: int
    name: str
    price: float
    stock: int
    category: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    def is_in_stock(self, quantity: int = 1) -> bool:
        return self.stock >= quantity

    def apply_discount(self, percent: float) -> float:
        return self.price * (1 - percent / 100)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "stock": self.stock,
            "category": self.category,
            "description": self.description,
        }
