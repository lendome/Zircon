from typing import Any, Dict, List
import json


def safe_json_loads(data: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def paginate(items: List[Any], page: int = 1, per_page: int = 10) -> Dict[str, Any]:
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": items[start:end],
        "page": page,
        "per_page": per_page,
        "total": len(items),
        "pages": len(items) // per_page,
    }


def generate_slug(text: str) -> str:
    return text.lower().strip().replace(" ", "-")


def format_currency(amount: float, currency: str = "USD") -> str:
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency, currency)
    return f"{symbol}{amount}"


def retry_operation(operation, max_attempts: int = 3):
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception:
            if attempt == max_attempts - 1:
                raise
    return None
