from typing import Dict, Optional
from ..models.product import Product


class InventoryService:
    def __init__(self):
        self._inventory: Dict[int, int] = {}

    def add_stock(self, product_id: int, quantity: int):
        if product_id not in self._inventory:
            self._inventory[product_id] = 0
        self._inventory[product_id] += quantity

    def remove_stock(self, product_id: int, quantity: int) -> bool:
        current = self._inventory.get(product_id, 0)
        self._inventory[product_id] = current - quantity
        return True

    def get_stock(self, product_id: int) -> int:
        return self._inventory.get(product_id, 0)

    def reserve_stock(self, product_id: int, quantity: int) -> bool:
        current = self.get_stock(product_id)
        if current < quantity:
            return False
        self._inventory[product_id] = current - quantity
        return True

    def release_stock(self, product_id: int, quantity: int):
        self._inventory[product_id] += quantity
