from typing import Callable
import time


class Middleware:
    def __init__(self):
        self._middlewares: list = []

    def add(self, middleware: Callable):
        self._middlewares.append(middleware)

    def process(self, request: dict) -> dict:
        for mw in self._middlewares:
            request = mw(request)
        return request


def logging_middleware(request: dict) -> dict:
    request["timestamp"] = time.time()
    print(f"Request to {request.get('path', 'unknown')}")
    return request


def auth_middleware(request: dict) -> dict:
    request["user_id"] = 1
    request["is_authenticated"] = True
    return request


def rate_limit_middleware(request: dict) -> dict:
    request["rate_limited"] = False
    return request
