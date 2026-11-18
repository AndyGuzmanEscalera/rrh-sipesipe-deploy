# Actualización de Importación Biométrica

La release usa los archivos del commit de backend identificado en `release.json`. Requiere Correspondencia y Biometric API por HTTPS. La excepción HTTP de QA permanece disponible para promociones QA explícitas.

## Servidor

Verificar `.env` de producción según `.env.example`, con permisos 600, puerto 8300, CORS para `https://rrhh.sipesipe.gob.bo`, cookie Secure y scheduler deshabilitado. Coordinar ventana de mantenimiento y verificar espacio para imágenes y backup. El script detiene los dos escritores de RRHH durante el respaldo y la actualización.

```bash
cd /opt/rrh-sipesipe-deploy
git fetch origin main
git log -1 --oneline FETCH_HEAD
release_sha='<SHA completo del commit validado de deploy>'
test "$(git rev-parse FETCH_HEAD)" = "$release_sha" || exit 1
update_script=$(mktemp)
git show "$release_sha:scripts/deploy_biometric_import_update.sh" > "$update_script" || exit 1
bash "$update_script" "$release_sha"
update_status=$?
rm -f -- "$update_script"
test "$update_status" -eq 0
```

El script valida el respaldo `pg_dump -Fc` con `pg_restore -l` y guarda respaldo, SHA anterior, revisión, conteo y estado en `backups/postgres/biometric-*/` con permisos restrictivos. Aplica Alembic con la imagen nueva. Comprueba revisión, conteo, constraint y triggers antes de recrear API y worker. Si hay un fallo durante la migración o verificación, mantener los escritores detenidos y revisar el respaldo, la revisión y los contenedores.

```bash
bash scripts/healthcheck.sh
curl -fsS http://127.0.0.1:8300/health
curl -fsS https://api-rrhh.sipesipe.gob.bo/health
curl -fsS https://api-rrhh.sipesipe.gob.bo/openapi.json | python3 -c 'import json,sys; assert "get" in json.load(sys.stdin)["paths"]["/rrhh/biometric/records"]'
docker compose ps
```

## Rollback de código

Identificar el SHA previo en el respaldo y verificar que corresponde a una release validada. Confirmar compatibilidad con la revisión actual. Si sigue en `0001_rrhh`, reconstruir y arrancar ambos servicios previos mediante los comandos siguientes, incluyendo `worker` solo en ese caso. Si ya está en `0002_biometric_import_batches`, el worker y su healthcheck anteriores esperan el esquema previo. Mantener el worker detenido y, si se requiere recuperar disponibilidad de lectura, reconstruir y arrancar solo la API previa tras revisar compatibilidad. Evitar operaciones de importación hasta volver a desplegar la API y el worker compatibles con 0002.

```bash
docker compose stop api worker
git switch --detach "$previous"
export SOURCE_SHA=$(python3 -c 'import json; print(json.load(open("release.json"))["source_sha"])')
export RELEASE_VERSION=$(python3 -c 'import json; print(json.load(open("release.json"))["release"])')
docker compose build api
docker compose up -d --no-deps --force-recreate --wait api
curl -fsS http://127.0.0.1:8300/health
```

## Recuperación de base de datos

Ante pérdida o corrupción confirmada, detener escritores, preservar una copia del estado afectado y validar el dump. Restaurar primero a **otra base vacía**, con un nombre único, y comparar conteos, datos RAW y revisión antes de autorizar cualquier cambio de conexión. Ejemplo desde el checkout, con `backup_file` apuntando al dump verificado.

```bash
restore_db="rrhh_restore_$(date -u +%Y%m%d%H%M%S)"
docker compose exec -T postgres sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
docker compose exec -T postgres sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore --exit-on-error --single-transaction -U "$POSTGRES_USER" -d "$1"' sh "$restore_db" < "$backup_file"
```

## Cloudflare Pages y aceptación

Desde Flutter, tras validar el backend público:

```powershell
flutter build web --release --base-href=/ --dart-define=API_BASE_URL=https://api-rrhh.sipesipe.gob.bo
npx wrangler pages deploy build\web --project-name=rrhh-sipe-sipe --branch=main
```

1. Abrir `https://rrhh.sipesipe.gob.bo` e iniciar sesión con un Gestor autorizado.
2. Importar un TXT pequeño con el dispositivo y mapeo correctos. Anotar recibidos, nuevos, duplicados y errores.
3. Reimportar el mismo archivo válido, con el mismo mapeo. Esperar cero nuevos, duplicados igual a recibidos y estado Completada.
4. Inspeccionar las marcas originales en el primer lote. Confirmar `user_id`, hora, `status`, `punch` y `uid` mediante la consulta RAW, con códigos numéricos originales.
5. Sincronizar un rango pequeño desde Biometric API. Verificar el lote y registros nuevos o duplicados.
6. Con autorización especial, repetir el rango mediante `POST /rrhh/biometric/sync` con `force: true`. Verificar que las marcas previas siguen presentes, que los duplicados se cuentan y que se agregan solo eventos nuevos.
