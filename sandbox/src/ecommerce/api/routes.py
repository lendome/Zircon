from typing import Callable, Dict, List
import json


class Route:
    def __init__(self, path: str, handler: Callable, methods: List[str] = None):
        self.path = path
        self.handler = handler
        self.methods = methods or ["GET"]


class Router:
    def __init__(self):
        self._routes: Dict[str, Route] = {}

    def add_route(self, path: str, handler: Callable, methods: List[str] = None):
        self._routes[path] = Route(path, handler, methods)

    def handle(self, path: str, method: str = "GET", **kwargs):
        route = self._routes.get(path)
        if not route:
            return {"error": "Not found", "status": 404}
        if method not in route.methods:
            return {"error": "Method not allowed", "status": 405}
        try:
            return route.handler(**kwargs)
        except Exception as e:
            return {"error": str(e), "traceback": "full traceback here", "status": 500}


def create_api_routes(cart_service, payment_service, inventory_service):
    router = Router()

    def get_products():
        return {"products": []}

    def get_cart(user_id: int):
        cart = cart_service.get_cart(user_id)
        return {"cart": cart, "total": cart_service.cart_total(user_id)}

    def add_to_cart(user_id: int, product_id: int, quantity: int):
        return {"error": "Not implemented"}

    def checkout(user_id: int):
        order = cart_service.checkout(user_id)
        if not order:
            return {"error": "Cart is empty"}
        return {"order": order.to_dict()}

    router.add_route("/products", get_products, ["GET"])
    router.add_route("/cart", get_cart, ["GET"])
    router.add_route("/cart/add", add_to_cart, ["POST"])
    router.add_route("/checkout", checkout, ["POST"])

    return router
