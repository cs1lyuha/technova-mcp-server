"""Autentificare și autorizare.

Roluri (least privilege):
- `customer`: citește doar comenzile firmei lui (`customer_idno` din token);
- `support`: citește tot și poate crea cereri de retur;
- `admin`: în plus, poate schimba statusul comenzilor.

Tokenii nu se țin în clar: fișierul de configurare conține doar SHA-256-ul
lor. Pe stdio tokenul vine din variabila de mediu `TECHNOVA_TOKEN`; pe HTTP,
din antetul `Authorization: Bearer …` al fiecărei cereri.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

ROLES = ("customer", "support", "admin")
PERMISSIONS = {
    "customer": {"read_own"},
    "support": {"read_all", "create_return"},
    "admin": {"read_all", "create_return", "update_status"},
}


class AuthError(Exception):
    """Token lipsă sau invalid."""


class Forbidden(Exception):
    """Token valid, dar rolul nu are voie."""


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    customer_idno: str | None = None

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS[self.role]

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise Forbidden(f"rolul '{self.role}' nu are dreptul '{permission}'")

    def can_see(self, customer_idno: str) -> bool:
        return self.can("read_all") or (self.can("read_own") and customer_idno == self.customer_idno)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class TokenStore:
    """{sha256(token): {"name": ..., "role": ..., "customer_idno": ...}}"""

    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "TokenStore":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def add(self, token: str, name: str, role: str, customer_idno: str | None = None) -> None:
        if role not in ROLES:
            raise ValueError(f"rol necunoscut: {role}")
        if role == "customer" and not customer_idno:
            raise ValueError("rolul customer cere customer_idno")
        self.entries[hash_token(token)] = {"name": name, "role": role, "customer_idno": customer_idno}

    def resolve(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("lipsește tokenul de acces")
        digest = hash_token(token)
        for known, entry in self.entries.items():
            if hmac.compare_digest(known, digest):  # comparație în timp constant
                return Principal(entry["name"], entry["role"], entry.get("customer_idno"))
        raise AuthError("token invalid")


def bearer(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    return value[7:].strip() if value.lower().startswith("bearer ") else None


@dataclass
class RateLimiter:
    """Token bucket per principal: `rate` apeluri pe minut, cu rafale de până la `burst`."""

    rate: float = 60.0
    burst: int = 20
    clock: callable = time.monotonic
    buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = self.clock()
        tokens, last = self.buckets.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate / 60.0)
        if tokens < 1:
            self.buckets[key] = (tokens, now)
            return False
        self.buckets[key] = (tokens - 1, now)
        return True
