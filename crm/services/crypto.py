import base64
import hashlib
import json
from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    raw = settings.APP_ENCRYPTION_KEY
    if not raw:
        raise RuntimeError("APP_ENCRYPTION_KEY belum diatur")
    try:
        return Fernet(raw.encode())
    except Exception:
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(derived)


def encrypt_dict(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_dict(value: str) -> dict:
    if not value:
        return {}
    return json.loads(_fernet().decrypt(value.encode()).decode())
