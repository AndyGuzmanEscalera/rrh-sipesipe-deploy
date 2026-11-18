#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
trap 'printf "Actualización detenida en la línea %s. Revise el estado y el respaldo antes de reanudar.\n" "$LINENO" >&2' ERR

cd /opt/rrh-sipesipe-deploy
[[ $(git rev-parse --show-toplevel) == "$PWD" ]] || die 'Directorio Git incorrecto'
[[ $(git branch --show-current) == main ]] || die 'Se requiere la rama main'
[[ -z $(git status --porcelain -- . ':(exclude)backups/postgres') ]] || die 'El checkout tiene cambios locales'
[[ -f .env && -r .env && ! -L .env ]] || die 'Se requiere un .env local legible'
docker info >/dev/null
docker compose version >/dev/null
command -v curl >/dev/null
command -v flock >/dev/null
exec 9>>.operation.lock
flock -n 9 || die 'Ya hay una actualización activa'

expected=${1:-}
[[ $expected =~ ^[0-9a-f]{40}$ ]] || die 'Indique el SHA completo del deploy publicado'
git fetch origin main
[[ $(git rev-parse FETCH_HEAD) == "$expected" ]] || die 'El SHA de origin/main difiere del esperado'
previous=$(git rev-parse HEAD)
git merge-base --is-ancestor "$previous" "$expected" || die 'La actualización no es fast-forward'

release_info=$(git show "$expected:release.json" | python3 -c '
import json, re, sys
m = json.load(sys.stdin)
sha = m["source_sha"]
if not re.fullmatch("[0-9a-f]{40}", sha) or m["release"] != "qa-" + sha[:12] or m["status"] != "validated":
    raise SystemExit("Release sin validación o identidad inválida")
print(sha, m["release"])
')
read -r SOURCE_SHA RELEASE_VERSION <<< "$release_info"
export SOURCE_SHA RELEASE_VERSION
export RUNTIME_ENV_FILE="$PWD/.env"
compose() { docker compose --project-name rrhh-api-deploy --env-file .env "$@"; }
python3 -B -c '
import sys
sys.path.insert(0, "scripts")
from operations import dotenv, validate_env, require
v = validate_env(dotenv(".env"))
require(v["APP_ENV"] == "production" and v.get("RRHH_API_PORT","8300") == "8300", "Configuración de producción y puerto 8300 requeridos")
require("https://rrhh.sipesipe.gob.bo" in [x.strip() for x in v.get("CORS_ALLOW_ORIGINS","").split(",")], "Falta el origen del frontend en CORS")
require(v["CORRESPONDENCIA_API_URL"].rstrip("/") == "https://api-correspondencia.sipesipe.gob.bo", "URL de Correspondencia incorrecta")
require(v["BIOMETRIC_API_URL"].rstrip("/") == "https://biometric.sipesipe.gob.bo", "URL biométrica incorrecta")
'
compose config --quiet
sql() {
    compose exec -T postgres sh -ec '
        export PGPASSWORD="$POSTGRES_PASSWORD"
        exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
    ' sh "$1"
}
pg=$(compose ps -q postgres)
[[ -n $pg && $(docker inspect --format '{{.State.Health.Status}}' "$pg") == healthy ]] || die 'PostgreSQL no está healthy'
for svc in api worker; do
    [[ -n $(compose ps -aq "$svc") ]] || die "Falta el contenedor previo de $svc"
done
target=0002_biometric_import_batches
revision=$(sql 'SELECT version_num FROM alembic_version')
[[ $revision == 0001_rrhh || $revision == "$target" ]] || die "Revisión previa inesperada: $revision"

[[ ! -L backups && ! -L backups/postgres ]] || die 'Ruta de respaldos enlazada'
install -d -m 700 backups backups/postgres
chmod 700 backups backups/postgres
backup_dir=$(mktemp -d "backups/postgres/biometric-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
compose ps -a > "$backup_dir/containers.txt"
# Detener escritores de RRHH para estabilizar el conteo y el respaldo.
compose stop api worker
before=$(sql 'SELECT count(*) FROM biometric_records')
[[ $before =~ ^[0-9]+$ ]] || die 'Conteo previo inválido'
printf 'sha=%s\nrevision=%s\nraw_count=%s\npostgres_container=%s\n' "$previous" "$revision" "$before" "$pg" > "$backup_dir/before.txt"
printf 'Respaldo y estado previo en %s\n' "$backup_dir"
compose exec -T postgres sh -ec '
    export PGPASSWORD="$POSTGRES_PASSWORD"
    exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"
' > "$backup_dir/rrhh.dump.partial"
compose exec -T postgres pg_restore -l < "$backup_dir/rrhh.dump.partial" > "$backup_dir/rrhh.toc"
grep -q 'TABLE DATA public biometric_records' "$backup_dir/rrhh.toc" || die 'El respaldo carece de datos RAW'
mv "$backup_dir/rrhh.dump.partial" "$backup_dir/rrhh.dump"

git merge --ff-only "$expected"
[[ $(git rev-parse HEAD) == "$expected" ]] || die 'HEAD distinto del SHA esperado'
compose config --quiet
compose build api worker
compose run --rm --no-deps api python -m alembic upgrade head
[[ $(sql 'SELECT version_num FROM alembic_version') == "$target" ]] || die 'Revisión final incorrecta'
after=$(sql 'SELECT count(*) FROM biometric_records')
[[ $after =~ ^[0-9]+$ && $after -ge $before ]] || die 'Disminuyó el conteo RAW'
[[ $(sql "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='biometric_records' AND column_name IN ('source_file_name','import_batch_id','raw_payload')) OR (table_name='biometric_syncs' AND column_name IN ('origin','source_file_name','error_records')))") == 6 ]] || die 'Faltan columnas de importación'
[[ $(sql "SELECT count(*) FROM pg_constraint WHERE conrelid='biometric_records'::regclass AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (record_id)'") == 1 ]] || die 'Falta UNIQUE(record_id)'
[[ $(sql "SELECT count(*) FROM pg_trigger WHERE tgrelid='biometric_records'::regclass AND tgenabled IN ('O','A') AND tgname IN ('rrhh_raw_evidence_guard','rrhh_raw_import_metadata_guard')") == 2 ]] || die 'Falta protección RAW'
compose up -d --no-deps --force-recreate --wait --wait-timeout 150 api worker
[[ $(compose ps -q postgres) == "$pg" ]] || die 'Cambió el contenedor PostgreSQL'
for url in http://127.0.0.1:8300/health https://api-rrhh.sipesipe.gob.bo/health; do
    response=$(curl -fsS --retry 6 --retry-delay 3 --max-time 10 "$url")
    grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' <<< "$response" || die "Falló health en $url"
done
compose logs --no-color --tail 100 api worker > "$backup_dir/runtime.log"
if grep -Eq 'Traceback \(most recent call last\)|CRITICAL|PANIC' "$backup_dir/runtime.log"; then
    die "Señal crítica en logs. Revise $backup_dir/runtime.log de forma privada"
fi
printf 'Actualizado %s. Revisión %s. RAW %s → %s. Respaldo: %s/rrhh.dump\n' "$expected" "$target" "$before" "$after" "$backup_dir"
