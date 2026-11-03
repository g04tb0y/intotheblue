"""Google Fast Pair Key-based Pairing — crypto and message codecs (active side).

This is the *protocol* counterpart to the passive ``bledev/fastpair.py`` (which only
maps a 24-bit Model ID to a product name). Here we build and parse the encrypted
Key-based Pairing blocks that drive the Fast Pair GATT handshake, as **Seeker** (the
central writing to a Provider) and, for the in-house positive control, as **Provider**
(``emulate.py``'s emulated accessory) — a symmetric round-trip so the whole exchange is
verifiable headless, with no radio.

Why a standalone module with **no blatann import**: the crypto is shared by two
independent front-ends — the Seeker lives in ``webui/engine.py`` (held-open GATT
session), the Provider in ``emulate.py`` (GATT server) — and must be unit-testable
without hardware. Keeping it blatann-free mirrors ``bledev/fastpair.py``/``manufacturers``.

Byte layout and crypto are taken verbatim from Google's spec (fetched 2026-11-03):

  developers.google.com/nearby/fast-pair/specifications/characteristics
  developers.google.com/nearby/fast-pair/specifications/service/gatt

Characteristics (128-bit, base ``FE2Cxxxx-8366-4814-8EB0-01DE32100BEA``):
  - Key-based Pairing  …1234  Write + Notify (not GATT-encrypted)
  - Passkey            …1235  Write + Notify
  - Account Key        …1236  Write
  - Additional Data    …1237  Write + Notify

Key-based Pairing **request** written by the Seeker:
  octet 0-15  : encrypted 16-byte block (mandatory)
  octet 16-79 : Seeker's public key, raw secp256r1 point X‖Y, 64 bytes (optional,
                present on first pairing / Case 1 so the Provider can run ECDH)

Raw request block, message type ``0x00`` (what the 16 bytes decrypt to):
  octet 0     : 0x00  (Key-based Pairing Request)
  octet 1     : flags (MSB-first; bit1 "initiate bonding, Seeker BR/EDR addr present")
  octet 2-7   : Provider's current BLE address or public address (big-endian)
  octet 8-13  : Seeker's BR/EDR address (only if flag bit1 or bit3 set)
  octet n-15  : random salt filling the block to 16 bytes

Raw response block, message type ``0x01`` (encrypted → notified back by the Provider):
  octet 0     : 0x01  (Key-based Pairing Response)
  octet 1-6   : Provider's public (BR/EDR) address (big-endian)
  octet 7-15  : random salt

Shared-secret derivation (Case 1, first pairing): ECDH over **secp256r1** between the
Seeker's ephemeral key pair and the Provider's anti-spoofing public key yields a 256-bit
secret (the X coordinate); **SHA-256** it and take the **first 16 bytes** as the
**AES-128** key. Encryption is **AES-128 in ECB mode**, a single 16-byte block, no IV.
"""
from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- characteristic UUIDs (lowercase, full 128-bit) ------------------------------
KBP_UUID = "fe2c1234-8366-4814-8eb0-01de32100bea"
PASSKEY_UUID = "fe2c1235-8366-4814-8eb0-01de32100bea"
ACCOUNT_KEY_UUID = "fe2c1236-8366-4814-8eb0-01de32100bea"
ADDITIONAL_DATA_UUID = "fe2c1237-8366-4814-8eb0-01de32100bea"
SERVICE_UUID16 = "fe2c"

# uuid (lowercase) -> human name, for the passive profile surfacing (item 1)
FP_CHARACTERISTICS: dict[str, str] = {
    KBP_UUID: "Key-based Pairing",
    PASSKEY_UUID: "Passkey",
    ACCOUNT_KEY_UUID: "Account Key",
    ADDITIONAL_DATA_UUID: "Additional Data",
}

# message-type nibbles (raw block octet 0)
MSG_KBP_REQUEST = 0x00
MSG_KBP_RESPONSE = 0x01
MSG_SEEKER_PASSKEY = 0x02
MSG_PROVIDER_PASSKEY = 0x03

_CURVE = ec.SECP256R1()


# --- key agreement ---------------------------------------------------------------
def gen_keypair() -> ec.EllipticCurvePrivateKey:
    """Generate a fresh secp256r1 key pair (Seeker ephemeral, or Provider anti-spoofing)."""
    return ec.generate_private_key(_CURVE)


def public_key_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """Export the public key as the raw 64-byte Fast Pair point ``X‖Y`` (no 0x04 prefix)."""
    numbers = private_key.public_key().public_numbers()
    return numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def _load_public(point64: bytes) -> ec.EllipticCurvePublicKey:
    """Rebuild a public key from the raw 64-byte ``X‖Y`` point (prepend the 0x04 tag)."""
    if len(point64) != 64:
        raise ValueError(f"public key must be 64 bytes (X‖Y), got {len(point64)}")
    return ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, b"\x04" + point64)


def derive_key(private_key: ec.EllipticCurvePrivateKey, peer_public64: bytes) -> bytes:
    """Derive the 16-byte AES-128 key: ECDH(secp256r1) → X coord → SHA-256 → first 16 bytes.

    Symmetric: the Seeker passes its ephemeral private key + the Provider's anti-spoofing
    public key; the Provider passes its anti-spoofing private key + the Seeker's public
    key. Both reach the same shared secret, hence the same AES key.
    """
    shared = private_key.exchange(ec.ECDH(), _load_public(peer_public64))  # 32-byte X
    return hashlib.sha256(shared).digest()[:16]


# --- AES-128-ECB single block ----------------------------------------------------
def aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    """Encrypt exactly one 16-byte block with AES-128-ECB (no IV, no padding)."""
    if len(key) != 16:
        raise ValueError("AES key must be 16 bytes")
    if len(block) != 16:
        raise ValueError("block must be exactly 16 bytes")
    return Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(block)


def aes_decrypt_block(key: bytes, block: bytes) -> bytes:
    """Decrypt exactly one 16-byte block with AES-128-ECB."""
    if len(key) != 16:
        raise ValueError("AES key must be 16 bytes")
    if len(block) != 16:
        raise ValueError("block must be exactly 16 bytes")
    return Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(block)


# --- address helpers -------------------------------------------------------------
def addr_to_bytes(address: str) -> bytes:
    """``'AA:BB:CC:DD:EE:FF'`` → 6 big-endian bytes (drops any blatann ``,r`` suffix)."""
    hexpart = address.split(",")[0].replace(":", "").strip()
    raw = bytes.fromhex(hexpart)
    if len(raw) != 6:
        raise ValueError(f"not a 6-byte BT address: {address!r}")
    return raw


def bytes_to_addr(raw: bytes) -> str:
    """6 big-endian bytes → ``'AA:BB:CC:DD:EE:FF'``."""
    if len(raw) != 6:
        raise ValueError("address must be 6 bytes")
    return ":".join(f"{b:02X}" for b in raw)


# --- Key-based Pairing request (Seeker builds, Provider parses) ------------------
def build_kbp_request(key: bytes, provider_addr: str, *, seeker_addr: str | None = None,
                      flags: int = 0, public_key64: bytes | None = None,
                      salt: bytes | None = None) -> bytes:
    """Build the full Key-based Pairing write: encrypted 16-byte block ``+`` optional pubkey.

    ``provider_addr`` goes at octets 2-7. If ``seeker_addr`` is given, flag bit1 (0x40,
    MSB-first) is set and it occupies octets 8-13; the remaining octets are random salt.
    Pass ``public_key64`` (the Seeker's raw point) to append it for a first pairing so the
    Provider can run ECDH. ``salt`` is only an injection point for tests/replay stress.
    """
    raw = bytearray(16)
    raw[0] = MSG_KBP_REQUEST
    if seeker_addr is not None:
        flags |= 0x40                                   # bit1 MSB-first → initiate bonding
    raw[1] = flags & 0xFF
    raw[2:8] = addr_to_bytes(provider_addr)
    tail = 8
    if seeker_addr is not None:
        raw[8:14] = addr_to_bytes(seeker_addr)
        tail = 14
    fill = salt if salt is not None else os.urandom(16 - tail)
    if len(fill) != 16 - tail:
        raise ValueError(f"salt must be {16 - tail} bytes for these flags")
    raw[tail:16] = fill
    block = aes_encrypt_block(key, bytes(raw))
    return block + (public_key64 or b"")


def parse_kbp_request(key: bytes, written: bytes) -> dict:
    """Provider side: decrypt+parse a Key-based Pairing write. Raises ``ValueError`` on bad input.

    Returns ``{"type", "flags", "provider_addr", "seeker_addr"|None, "salt",
    "public_key"|None}``. The caller validates ``provider_addr`` against its own address
    (the spec's anti-spoofing check) before acting.
    """
    if len(written) not in (16, 80):
        raise ValueError(f"KBP write must be 16 or 80 bytes, got {len(written)}")
    block = aes_decrypt_block(key, written[:16])
    if block[0] != MSG_KBP_REQUEST:
        raise ValueError(f"not a Key-based Pairing Request (type 0x{block[0]:02x})")
    flags = block[1]
    out = {
        "type": block[0],
        "flags": flags,
        "provider_addr": bytes_to_addr(block[2:8]),
        "seeker_addr": None,
        "public_key": written[16:80] if len(written) == 80 else None,
    }
    if flags & 0x40:                                    # Seeker BR/EDR address present
        out["seeker_addr"] = bytes_to_addr(block[8:14])
        out["salt"] = block[14:16]
    else:
        out["salt"] = block[8:16]
    return out


# --- Key-based Pairing response (Provider builds, Seeker parses) -----------------
def build_kbp_response(key: bytes, provider_public_addr: str, *, salt: bytes | None = None) -> bytes:
    """Provider side: build the encrypted 16-byte response notified back to the Seeker."""
    raw = bytearray(16)
    raw[0] = MSG_KBP_RESPONSE
    raw[1:7] = addr_to_bytes(provider_public_addr)
    raw[7:16] = salt if salt is not None else os.urandom(9)
    if len(raw[7:16]) != 9:
        raise ValueError("response salt must be 9 bytes")
    return aes_encrypt_block(key, bytes(raw))


def parse_kbp_response(key: bytes, notified: bytes) -> dict:
    """Seeker side: decrypt+validate the notified response. Raises ``ValueError`` on bad input.

    A failed decryption / wrong message type is a *valid finding* for the caller (the
    Provider rejected us), surfaced as an exception the engine reports faithfully.
    """
    if len(notified) != 16:
        raise ValueError(f"KBP response must be 16 bytes, got {len(notified)}")
    block = aes_decrypt_block(key, notified)
    if block[0] != MSG_KBP_RESPONSE:
        raise ValueError(f"not a Key-based Pairing Response (type 0x{block[0]:02x})")
    return {
        "type": block[0],
        "provider_addr": bytes_to_addr(block[1:7]),
        "salt": block[7:16],
    }
