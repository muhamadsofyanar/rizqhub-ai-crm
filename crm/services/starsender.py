from __future__ import annotations

from typing import Any

import httpx
from django.utils import timezone

from crm.models import (
    ChannelConnection,
    StarSenderAccount,
    StarSenderDevice,
    WhatsAppGroup,
)
from crm.services.crypto import decrypt_dict, encrypt_dict


BASE_URL = "https://api.starsender.online/api"


class StarSenderError(RuntimeError):
    """Structured provider error used to avoid unsafe duplicate retries.

    ``retryable`` means the request is known not to have reached the provider
    (for example a connection failure) or the provider explicitly asks the
    client to retry (HTTP 429). ``uncertain`` means the provider may have
    accepted the message even though the client did not receive a definitive
    response. Uncertain sends must be reconciled manually, not retried
    automatically.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        uncertain: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.uncertain = uncertain


def _request(method: str, path: str, api_key: str, *, payload: dict | None = None, timeout: int = 45) -> dict:
    if not api_key:
        raise StarSenderError("Credential StarSender belum diisi")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method,
                f"{BASE_URL}{path}",
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json=payload,
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise StarSenderError(
            f"Tidak dapat terhubung ke StarSender: {exc}",
            retryable=True,
        ) from exc
    except httpx.RequestError as exc:
        # A timeout/read/write failure after connection establishment can occur
        # after StarSender has already accepted the message. Retrying would risk
        # sending a duplicate.
        raise StarSenderError(
            f"Status pengiriman StarSender tidak dapat dipastikan: {exc}",
            uncertain=True,
        ) from exc
    if response.is_error:
        status_code = response.status_code
        raise StarSenderError(
            f"StarSender HTTP {status_code}: {response.text[:800]}",
            status_code=status_code,
            retryable=status_code == 429,
            uncertain=status_code >= 500,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise StarSenderError(f"Respons StarSender bukan JSON: {response.text[:500]}") from exc
    if isinstance(data, dict) and data.get("success") is False:
        raise StarSenderError(str(data)[:1200])
    return data if isinstance(data, dict) else {"data": data}


def _account_key(account: StarSenderAccount) -> str:
    try:
        return decrypt_dict(account.encrypted_account_key).get("account_api_key", "")
    except Exception:
        return ""


def _device_key(device: StarSenderDevice) -> str:
    try:
        return decrypt_dict(device.encrypted_device_key).get("device_key", "")
    except Exception:
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return "Belum diisi"
    if len(value) <= 8:
        return "••••••••"
    return f"{value[:3]}••••••••{value[-4:]}"


def account_key_mask(account: StarSenderAccount) -> str:
    return mask_secret(_account_key(account))


def device_key_mask(device: StarSenderDevice) -> str:
    return mask_secret(_device_key(device))


def test_account(account: StarSenderAccount) -> dict:
    return _request("GET", "/devices", _account_key(account))


def test_device(device: StarSenderDevice) -> dict:
    return _request("GET", "/whatsapp/groups", _device_key(device))


def _extract_collection(data: Any, candidate_keys: tuple[str, ...]) -> list[dict]:
    """Extract list-like StarSender responses without assuming one response shape.

    StarSender documentation shows ``data.devices`` for devices, while group
    responses may be a list, a mapping of ids to objects, or a mapping of group
    names to JIDs. This parser deliberately ignores status/message envelopes.
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict) or not data:
        return []

    identity_keys = {
        "id", "device_id", "deviceId", "uuid", "jid", "group_id",
        "groupId", "remoteJid", "name", "subject", "device_name",
    }
    if identity_keys.intersection(data.keys()):
        return [data]

    for key in candidate_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_collection(value, candidate_keys)
            if nested:
                return nested

    dict_values = [value for value in data.values() if isinstance(value, dict)]
    if dict_values and len(dict_values) == len(data):
        rows = []
        for map_key, value in data.items():
            item = dict(value)
            item.setdefault("id", map_key)
            rows.append(item)
        return rows

    # Some group endpoints return {"Nama Grup": "1203...@g.us"}.
    envelope_keys = {"success", "status", "message", "error", "code"}
    if not envelope_keys.intersection(data.keys()) and all(
        isinstance(value, (str, int)) for value in data.values()
    ):
        return [
            {"name": str(name), "id": str(identifier)}
            for name, identifier in data.items()
        ]

    for value in data.values():
        if isinstance(value, (dict, list)):
            nested = _extract_collection(value, candidate_keys)
            if nested:
                return nested
    return []


def _first(item: dict, *keys: str, default=""):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_status(value: Any) -> str:
    if value is True or value == 1:
        return "connected"
    if value is False or value == 0:
        return "disconnected"
    raw = str(value or "").strip().lower()
    if any(word in raw for word in ("connect", "ready", "online", "authenticated")):
        return "connected"
    if any(word in raw for word in ("scan", "qr")):
        return "scanning"
    if any(word in raw for word in ("disconnect", "offline", "logout", "not connected")):
        return "disconnected"
    return "unknown"


def list_devices(account: StarSenderAccount) -> tuple[list[dict], dict]:
    raw = _request("GET", "/devices", _account_key(account))
    rows = _extract_collection(raw.get("data", raw), ("devices", "rows", "items", "data"))
    return rows, raw


def ensure_device_connection(device: StarSenderDevice):
    """Create/update the legacy ChannelConnection used by inbox and message tasks."""
    if not device.brand_id:
        return None
    credentials = {"api_key": _device_key(device)}
    connection = device.connection
    if not connection:
        connection = ChannelConnection.objects.create(
            tenant=device.tenant,
            brand=device.brand,
            agent=device.agent,
            provider="starsender",
            name=f"StarSender — {device.name or device.phone_number or device.external_device_id}",
            external_id=device.external_device_id,
            encrypted_credentials=encrypt_dict(credentials),
            settings={"managed_by_starsender_device": True},
            is_active=device.send_enabled,
        )
        device.connection = connection
        device.save(update_fields=["connection", "updated_at"])
        return connection

    dirty = []
    if connection.brand_id != device.brand_id:
        connection.brand = device.brand
        dirty.append("brand")
    if connection.agent_id != device.agent_id:
        connection.agent = device.agent
        dirty.append("agent")
    if connection.external_id != device.external_device_id:
        connection.external_id = device.external_device_id
        dirty.append("external_id")
    expected_name = f"StarSender — {device.name or device.phone_number or device.external_device_id}"
    if connection.name != expected_name:
        connection.name = expected_name
        dirty.append("name")
    key = _device_key(device)
    existing_key = ""
    if connection.encrypted_credentials:
        try:
            existing_key = decrypt_dict(connection.encrypted_credentials).get("api_key", "")
        except Exception:
            existing_key = ""
    if key and existing_key != key:
        connection.encrypted_credentials = encrypt_dict(credentials)
        dirty.append("encrypted_credentials")
    if connection.is_active != device.send_enabled:
        connection.is_active = device.send_enabled
        dirty.append("is_active")
    if dirty:
        connection.save(update_fields=[*dirty, "updated_at"])
    return connection


def sync_devices(account: StarSenderAccount) -> dict:
    rows, raw = list_devices(account)
    seen = set()
    created = 0
    updated = 0
    for item in rows:
        external_id = str(_first(item, "id", "device_id", "deviceId", "uuid", "key")).strip()
        if not external_id:
            continue
        seen.add(external_id)
        name = str(_first(item, "name", "device_name", "deviceName", "label")).strip()
        phone = str(_first(item, "number", "phone", "phone_number", "device", "wid")).strip()
        status = _normalize_status(_first(item, "status", "connection_status", "state", "connected"))
        device, was_created = StarSenderDevice.objects.get_or_create(
            tenant=account.tenant,
            account=account,
            external_device_id=external_id,
            defaults={
                "name": name,
                "phone_number": phone,
                "connection_status": status,
                "metadata": item,
                "last_seen_at": timezone.now(),
            },
        )
        if was_created:
            created += 1
        else:
            device.name = name or device.name
            device.phone_number = phone or device.phone_number
            device.connection_status = status
            device.metadata = item
            device.last_seen_at = timezone.now()
            device.save(
                update_fields=[
                    "name",
                    "phone_number",
                    "connection_status",
                    "metadata",
                    "last_seen_at",
                    "updated_at",
                ]
            )
            updated += 1
        if device.brand_id:
            ensure_device_connection(device)

    if seen:
        StarSenderDevice.objects.filter(account=account).exclude(
            external_device_id__in=seen
        ).update(connection_status="unknown", updated_at=timezone.now())
    account.last_sync_at = timezone.now()
    account.last_sync_status = "success"
    account.last_error = ""
    account.save(update_fields=["last_sync_at", "last_sync_status", "last_error", "updated_at"])
    return {"created": created, "updated": updated, "total": len(rows), "raw": raw}


def list_groups(device: StarSenderDevice) -> tuple[list[dict], dict]:
    raw = _request("GET", "/whatsapp/groups", _device_key(device))
    rows = _extract_collection(raw.get("data", raw), ("groups", "rows", "items", "data"))
    return rows, raw


def sync_groups(device: StarSenderDevice) -> dict:
    rows, raw = list_groups(device)
    seen = set()
    created = 0
    updated = 0
    for item in rows:
        group_id = str(
            _first(item, "id", "jid", "group_id", "groupId", "remoteJid", "value")
        ).strip()
        name = str(_first(item, "name", "subject", "group_name", "groupName", "label")).strip()
        if not group_id:
            continue
        seen.add(group_id)
        group, was_created = WhatsAppGroup.objects.get_or_create(
            tenant=device.tenant,
            device=device,
            external_group_id=group_id,
            defaults={
                "name": name or group_id,
                "metadata": item,
                "last_synced_at": timezone.now(),
            },
        )
        if was_created:
            created += 1
        else:
            group.name = name or group.name
            group.metadata = item
            group.is_active = True
            group.last_synced_at = timezone.now()
            group.save(
                update_fields=["name", "metadata", "is_active", "last_synced_at", "updated_at"]
            )
            updated += 1
    if seen:
        WhatsAppGroup.objects.filter(device=device).exclude(external_group_id__in=seen).update(
            is_active=False, updated_at=timezone.now()
        )
    device.last_group_sync_at = timezone.now()
    device.last_error = ""
    device.save(update_fields=["last_group_sync_at", "last_error", "updated_at"])
    return {"created": created, "updated": updated, "total": len(rows), "raw": raw}


def send_personal(
    device: StarSenderDevice,
    to: str,
    body: str,
    file_url: str = "",
    *,
    delay_seconds: int = 0,
) -> dict:
    if not device.send_enabled:
        raise StarSenderError("Pengiriman device dinonaktifkan")
    if device.connection_status == "disconnected":
        raise StarSenderError("Device StarSender sedang disconnected")
    payload = {
        "messageType": "media" if file_url else "text",
        "to": to,
        "body": body,
    }
    if file_url:
        payload["file"] = file_url
    if delay_seconds:
        payload["delay"] = int(delay_seconds)
    return _request("POST", "/send", _device_key(device), payload=payload)


def send_group(
    device: StarSenderDevice,
    group_id: str,
    body: str,
    file_url: str = "",
    *,
    delay_seconds: int = 0,
) -> dict:
    if not device.send_enabled:
        raise StarSenderError("Pengiriman device dinonaktifkan")
    if device.connection_status == "disconnected":
        raise StarSenderError("Device StarSender sedang disconnected")
    payload = {
        "messageType": "media" if file_url else "text",
        "to": group_id,
        "body": body,
    }
    if file_url:
        payload["file"] = file_url
    if delay_seconds:
        payload["delay"] = int(delay_seconds)
    return _request("POST", "/send/grup", _device_key(device), payload=payload)
