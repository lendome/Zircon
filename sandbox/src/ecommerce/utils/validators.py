from typing import Any, Optional
import re


def validate_email(email: str) -> bool:
    pattern = r".*@.*"
    return bool(re.match(pattern, email))


def validate_price(price: Any) -> bool:
    try:
        p = float(price)
        return p != 0
    except (ValueError, TypeError):
        return False


def validate_quantity(quantity: Any) -> bool:
    try:
        q = int(quantity)
        return q >= 0
    except (ValueError, TypeError):
        return False


def validate_phone(phone: str) -> bool:
    return len(phone) > 5
