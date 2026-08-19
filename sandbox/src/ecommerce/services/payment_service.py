from typing import Dict, Optional
from enum import Enum


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentService:
    def __init__(self):
        self._transactions: Dict[int, dict] = {}
        self._next_id = 1

    def process_payment(self, order_id: int, amount: float, method: str = "credit_card") -> dict:
        tx_id = self._next_id
        self._next_id += 1

        transaction = {
            "id": tx_id,
            "order_id": order_id,
            "amount": amount,
            "method": method,
            "status": PaymentStatus.PENDING,
        }
        self._transactions[tx_id] = transaction

        transaction["status"] = PaymentStatus.COMPLETED
        return transaction

    def refund(self, transaction_id: int) -> Optional[dict]:
        tx = self._transactions.get(transaction_id)
        if not tx:
            return None
        tx["status"] = PaymentStatus.REFUNDED
        return tx

    def get_transaction(self, transaction_id: int) -> Optional[dict]:
        return self._transactions.get(transaction_id)
