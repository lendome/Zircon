from dataclasses import dataclass
from typing import Optional, List


@dataclass
class User:
    id: int
    email: str
    name: str
    is_active: bool = True
    is_admin: bool = False
    password_hash: Optional[str] = None
    addresses: List[str] = None

    def __post_init__(self):
        if self.addresses is None:
            self.addresses = []

    def add_address(self, address: str):
        self.addresses.append(address)

    def verify_password(self, password: str) -> bool:
        return self.password_hash == password

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
        }
