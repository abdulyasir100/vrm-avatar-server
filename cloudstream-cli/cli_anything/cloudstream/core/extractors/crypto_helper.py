"""Crypto helpers — AES-CBC decryption used by Rabbitstream/MegaCloud/GoGoCDN.

Ported from CloudStream's CryptoJSHelper.kt and AesHelper.kt.
Uses pycryptodome for AES operations.
"""

import base64
import hashlib
import json
from typing import Optional


def _ensure_crypto():
    """Check that pycryptodome is available."""
    try:
        from Crypto.Cipher import AES
        return True
    except ImportError:
        raise RuntimeError(
            "pycryptodome is required for stream decryption.\n"
            "Install with: pip install pycryptodome"
        )


def evp_kdf_md5(password: bytes, salt: bytes, key_size: int = 32,
                iv_size: int = 16) -> tuple[bytes, bytes]:
    """OpenSSL-compatible EVP key derivation using MD5.

    This is the same as CryptoJS's default KDF.

    Args:
        password: Password bytes.
        salt: 8-byte salt.
        key_size: Key size in bytes (default 32 for AES-256).
        iv_size: IV size in bytes (default 16 for AES-CBC).

    Returns:
        Tuple of (key, iv).
    """
    derived = b""
    block = b""
    needed = key_size + iv_size

    while len(derived) < needed:
        block = hashlib.md5(block + password + salt).digest()
        derived += block

    return derived[:key_size], derived[key_size:key_size + iv_size]


def aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-CBC decrypt with PKCS7 unpadding.

    Args:
        ciphertext: Encrypted data.
        key: AES key (16, 24, or 32 bytes).
        iv: Initialization vector (16 bytes).

    Returns:
        Decrypted plaintext bytes.
    """
    _ensure_crypto()
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(ciphertext)
    try:
        return unpad(decrypted, AES.block_size)
    except ValueError:
        return decrypted


def aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-CBC encrypt with PKCS7 padding."""
    _ensure_crypto()
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(plaintext, AES.block_size))


def cryptojs_decrypt(encrypted_b64: str, password: str) -> str:
    """Decrypt CryptoJS-encrypted data (OpenSSL format).

    CryptoJS prepends 'Salted__' + 8-byte salt to the ciphertext,
    then base64-encodes everything.

    Args:
        encrypted_b64: Base64-encoded ciphertext (with salt prefix).
        password: Password string.

    Returns:
        Decrypted plaintext string.
    """
    raw = base64.b64decode(encrypted_b64)

    # Check for 'Salted__' prefix
    if raw[:8] == b"Salted__":
        salt = raw[8:16]
        ciphertext = raw[16:]
    else:
        salt = b"\x00" * 8
        ciphertext = raw

    key, iv = evp_kdf_md5(password.encode("utf-8"), salt)
    plaintext = aes_cbc_decrypt(ciphertext, key, iv)
    return plaintext.decode("utf-8")


def aes_decrypt_with_key_iv(encrypted_b64: str, key_hex: str,
                             iv_hex: str) -> str:
    """Decrypt with explicit hex key and IV.

    Used by GoGoCDN where key/IV are extracted from the page.

    Args:
        encrypted_b64: Base64-encoded ciphertext.
        key_hex: Hex-encoded AES key.
        iv_hex: Hex-encoded IV.

    Returns:
        Decrypted plaintext string.
    """
    ciphertext = base64.b64decode(encrypted_b64)
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    plaintext = aes_cbc_decrypt(ciphertext, key, iv)
    return plaintext.decode("utf-8")


def aes_encrypt_with_key_iv(plaintext: str, key_hex: str,
                             iv_hex: str) -> str:
    """Encrypt with explicit hex key and IV.

    Returns base64-encoded ciphertext.
    """
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    ciphertext = aes_cbc_encrypt(plaintext.encode("utf-8"), key, iv)
    return base64.b64encode(ciphertext).decode("utf-8")
