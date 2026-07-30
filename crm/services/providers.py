import httpx
from django.conf import settings
from .crypto import decrypt_dict


class ProviderError(RuntimeError):
    pass


def send_starsender(connection, to: str, body: str, file_url: str = "") -> dict:
    credentials = decrypt_dict(connection.encrypted_credentials)
    api_key = credentials.get("api_key")
    if not api_key:
        raise ProviderError("API key StarSender belum diisi")
    payload = {
        "messageType": "media" if file_url else "text",
        "to": to,
        "body": body,
    }
    if file_url:
        payload["file"] = file_url
    with httpx.Client(timeout=45) as client:
        response = client.post(
            "https://api.starsender.online/api/send",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    if response.is_error:
        raise ProviderError(f"StarSender HTTP {response.status_code}: {response.text[:500]}")
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}
    if isinstance(data, dict) and data.get("success") is False:
        raise ProviderError(str(data))
    return data


def send_mailketing(connection, recipient: str, subject: str, content: str) -> dict:
    credentials = decrypt_dict(connection.encrypted_credentials)
    token = credentials.get("api_token")
    from_name = credentials.get("from_name") or connection.brand.name
    from_email = credentials.get("from_email")
    if not token or not from_email:
        raise ProviderError("API token atau from_email Mailketing belum diisi")
    payload = {
        "api_token": token,
        "from_name": from_name,
        "from_email": from_email,
        "recipient": recipient,
        "subject": subject,
        "content": content,
    }
    with httpx.Client(timeout=45) as client:
        response = client.post("https://api.mailketing.co.id/api/v1/send", data=payload)
    if response.is_error:
        raise ProviderError(f"Mailketing HTTP {response.status_code}: {response.text[:500]}")
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}
