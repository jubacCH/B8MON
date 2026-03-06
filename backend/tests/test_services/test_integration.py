"""Tests for integration service config encryption."""
from services.integration import encrypt_config, decrypt_config


def test_encrypt_decrypt_roundtrip():
    original = {"host": "10.0.0.1", "username": "admin", "password": "s3cret"}
    encrypted = encrypt_config(original)
    assert encrypted != str(original)
    decrypted = decrypt_config(encrypted)
    assert decrypted == original


def test_encrypt_produces_different_output():
    cfg = {"key": "value"}
    e1 = encrypt_config(cfg)
    e2 = encrypt_config(cfg)
    # Fernet uses random IV, so outputs differ
    assert e1 != e2
