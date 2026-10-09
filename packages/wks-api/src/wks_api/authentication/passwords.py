"""Salted scrypt passwords; high-entropy recovery secrets are stored as SHA-256 only."""

import hashlib
import hmac
import secrets

N, R, P = 32768, 8, 1


def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024**2)
    return f"scrypt${salt.hex()}${value.hex()}"


def password_matches(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=N, r=R, p=P, maxmem=64 * 1024**2
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def recovery_token():
    return "wks-recovery-" + secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


DUMMY_PASSWORD = password_hash(secrets.token_urlsafe(32))
