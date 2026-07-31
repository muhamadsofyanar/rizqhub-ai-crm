#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "Pemakaian: $0 NAMA_CONTAINER_POSTGRES [FOLDER_BACKUP]" >&2
  exit 2
fi

container="$1"
out_dir="${2:-/root/rizqhub-backups}"
stamp="$(date +%Y%m%d-%H%M%S)"
out_file="$out_dir/rizqhub-pre-upgrade-$stamp.dump"

mkdir -p "$out_dir"
if ! docker inspect "$container" >/dev/null 2>&1; then
  echo "Container tidak ditemukan: $container" >&2
  exit 1
fi

# Password tetap dibaca di dalam container; tidak ditulis ke command history host.
docker exec "$container" sh -lc '
  set -eu
  : "${POSTGRES_USER:?POSTGRES_USER tidak tersedia}"
  : "${POSTGRES_DB:?POSTGRES_DB tidak tersedia}"
  : "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD tidak tersedia}"
  export PGPASSWORD="$POSTGRES_PASSWORD"
  exec pg_dump --format=custom --no-owner --no-privileges \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
' > "$out_file"

if [ ! -s "$out_file" ]; then
  rm -f "$out_file"
  echo "Backup gagal atau file kosong." >&2
  exit 1
fi

# Validasi struktur archive tanpa melakukan restore atau mengubah database.
if ! docker exec -i "$container" pg_restore --list >/dev/null 2>&1 < "$out_file"; then
  rm -f "$out_file"
  echo "Backup tidak lolos validasi pg_restore --list." >&2
  exit 1
fi

sha256sum "$out_file" > "$out_file.sha256"
echo "Backup berhasil dan archive valid: $out_file"
echo "Checksum: $out_file.sha256"
