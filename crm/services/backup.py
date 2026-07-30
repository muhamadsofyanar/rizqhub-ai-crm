from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database_backup() -> dict:
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"rizqhub-{stamp}.dump"

    db_url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(db_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("Backup otomatis hanya mendukung PostgreSQL")

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        parsed.hostname or "postgres",
        "--port",
        str(parsed.port or 5432),
        "--username",
        parsed.username or "rizqhub",
        "--file",
        str(path),
        (parsed.path or "/rizqhub").lstrip("/"),
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr[-2000:] or "pg_dump gagal")

    cutoff = timezone.now() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    for old_file in backup_dir.glob("rizqhub-*.dump"):
        modified = timezone.make_aware(datetime.fromtimestamp(old_file.stat().st_mtime))
        if modified < cutoff:
            old_file.unlink(missing_ok=True)

    return {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "checksum_sha256": _sha256(path),
    }
