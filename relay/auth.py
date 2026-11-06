"""Token checks. Comparisons are constant-time and never logged."""

from __future__ import annotations

import hashlib
import hmac


def const_eq(presented: str, expected: str) -> bool:
    """Compare two strings without short-circuiting on the secret bytes.

    SHA-256 digests are fixed length, so ``hmac.compare_digest`` does not
    bail out early when the presented value and the secret differ in length.
    An empty expected secret never matches (fail closed).
    """
    if not isinstance(presented, str) or not isinstance(expected, str):
        return False
    if not expected:
        return False
    left = hashlib.sha256(presented.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def bearer_token(header: str | None) -> str | None:
    if not header or not isinstance(header, str):
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def relay_authorized(authorization_header: str | None, expected: str) -> bool:
    presented = bearer_token(authorization_header)
    if presented is None:
        return False
    return const_eq(presented, expected)


def secret_authorized(presented: str | None, expected: str) -> bool:
    if not isinstance(presented, str):
        return False
    return const_eq(presented, expected)
