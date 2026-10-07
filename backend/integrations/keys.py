"""Ed25519 key handling for integrations. RastiChat stores and handles public keys only."""
import secrets

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def load_public_key(pem):
    """Parse an Ed25519 public key (PEM SubjectPublicKeyInfo). ValueError for anything else —
    including private keys, which must never be pasted into RastiChat."""
    if not isinstance(pem, str) or not pem.strip():
        raise ValueError('A PEM public key is required.')
    if 'PRIVATE' in pem:
        raise ValueError('That is a PRIVATE key. Provide only the public key; never share the private key.')
    try:
        key = serialization.load_pem_public_key(pem.strip().encode())
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise ValueError(f'Not a valid PEM public key: {exc}') from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError('Only Ed25519 public keys are supported.')
    return key


def new_kid():
    return 'ick_' + secrets.token_hex(12)


def generate_keypair():
    """-> (private_pem, public_pem). For development/reference hosts and tests only: a production host generates
    its own keypair and sends RastiChat the public half."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem
