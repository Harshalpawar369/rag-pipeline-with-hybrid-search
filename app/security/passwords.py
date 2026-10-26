"""Password hashing with scrypt from the standard library (no extra dependency).

Stored format: scrypt$<n>$<r>$<p>$<salt hex>$<derived key hex>
"""
from __future__ import annotations

import hashlib
import hmac
import os

_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(dk_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, bytes.fromhex(dk_hex))


# Verified against when the account does not exist, so unknown emails cost the same time as wrong passwords.
DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")
