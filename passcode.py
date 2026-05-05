"""
Passcode hashing for the settings/quit guard.

PBKDF2-HMAC-SHA256, 200_000 iterations, 16-byte random salt.
Stored in config.json as passcode_salt (hex) and passcode_hash (hex).
"""
import hashlib
import secrets

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16


def hash_passcode(passcode: str) -> tuple[str, str]:
    """Return (salt_hex, hash_hex) for the given passcode."""
    salt = secrets.token_bytes(SALT_BYTES)
    h = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), h.hex()


def verify_passcode(passcode: str, salt_hex: str | None, hash_hex: str | None) -> bool:
    if not salt_hex or not hash_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", passcode.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return secrets.compare_digest(h.hex(), hash_hex)


def passcode_is_set(config: dict) -> bool:
    return bool(config.get("passcode_hash") and config.get("passcode_salt"))
