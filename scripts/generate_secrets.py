import base64
import os
import secrets

print("DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64))
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(36))
print("APP_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode())
print("ADMIN_PASSWORD=" + secrets.token_urlsafe(24))
