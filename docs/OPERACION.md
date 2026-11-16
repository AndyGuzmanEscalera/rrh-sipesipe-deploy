# Operación de RRHH independiente

## Entrega y trazabilidad

SOURCE: https://github.com/AndyGuzmanEscalera/rrhh-sipe-sipe-api, develop.
SHA promovido: f5affb69a1777289ab6f4fd8d27ae259b41895d1.
DEPLOY: https://github.com/AndyGuzmanEscalera/rrh-sipesipe-deploy, main.
El repositorio propuesto inicialmente rrhh-sipe-sipe-api-deploy se creó vacío
antes del cambio de nombre solicitado; no recibe runtime ni push de esta entrega.
No se eliminó ni reutilizó ese repositorio.

Estructura: .env.example, .gitignore, .gitattributes, docker-compose.yml,
release.json, backend/{Dockerfile,.dockerignore,requirements.txt,app/,alembic/,
alembic.ini}, scripts/{prepare_deploy.py,operations.py,preflight.sh,deploy.sh,
healthcheck.sh,rollback.sh}, docs/OPERACION.md y tests operativos propios.

## Entorno privado

La plantilla no contiene secretos reales. APP_ENV=production, DB propia rrhh_sipe,
POSTGRES_USER=rrhh y POSTGRES_PASSWORD privado (32 bytes hex o más).
Compose deriva DATABASE_URL hacia postgres:5432 dentro de su propia red.
No reutilizar passwords, volúmenes ni conexiones de Correspondencia/Gestión.

CORRESPONDENCIA_API_URL debe incluir el prefijo público real del maestro si existe;
TOKEN es opcional y no reemplaza la identidad del usuario; TIMEOUT=30.
No hay M2M dedicado acordado. No sustituirlo por una contraseña administrativa.
BIOMETRIC_API_URL=https://biometric.sipesipe.gob.bo, TOKEN vacío en plantilla,
TIMEOUT=60, DEVICE_ID=alcaldia-central. No hay pyzk ni conexión directa al reloj.
Tokens privados solo se cargan en el entorno autorizado, nunca se copian del PC.

Excepción QA actual: `APP_ENV=qa`, `ALLOW_PRIVATE_HTTP_CORRESPONDENCIA=true` y
`CORRESPONDENCIA_API_URL=http://backend:8000`. No se usan IPs Docker ni HTTPS público
para esa comunicación interna QA. Pydantic y preflight admiten exactamente ese
destino, sin credenciales/query/fragmento/path extra; no se admite HTTP público ni
HTTP en producción. El flag apagado es el default. API/worker se conectan a la red
externa existente correspondencia-deploy_edge además de sus redes RRHH; PostgreSQL
no se conecta allí. Preflight verifica que la red exista, sin crearla/modificarla.
En producción restaurar HTTPS estable y flag=false. El resto de CORS/cookies y
la URL biométrica permanecen sin cambios. Comandos: QA_CORRESPONDENCIA_INTERNA.md.

LOCAL_TIMEZONE=America/La_Paz; CORS_ALLOW_ORIGINS vacío para mismo origen, o lista
exacta de HTTPS autorizados. SESSION_COOKIE_SECURE=true, SameSite=lax, nombre
rrhh_refresh_token, ruta /auth cuando API se publica en raíz del dominio.
Si un proxy añade /api, ajustar cookie a /api/auth. No se cambia el router Flutter.
SCHEDULER_ENABLED=false y worker_poll5s; mantener así hasta cutover aprobado del
sincronizador anterior. WORKER_POLL_SECONDS no activa por sí mismo descargas.
Preflight rechaza scheduler activo en este primer paquete.

RRHH_ENV_FILE admite un archivo privado externo en vez de .env. El proyecto Compose
predeterminado es rrhh-api-deploy. RRHH_COMPOSE_PROJECT permite únicamente nombres
de ese proyecto con sufijo para pruebas locales aisladas, sin adoptar volúmenes.
No pasar el contenido secreto en argumentos shell, tickets ni logs.

## Comandos siguientes en Debian — documentados, NO ejecutados

```bash
git clone --branch main https://github.com/AndyGuzmanEscalera/rrh-sipesipe-deploy.git
cd rrh-sipesipe-deploy
umask 077
cp .env.example .env
chmod 600 .env
${EDITOR:-nano} .env
# Completar URL y secretos privados; revisar puerto8300 y CORS/cookie.
bash scripts/preflight.sh
python3 -B scripts/operations.py migrate --confirm-migrations --initial-empty-db
bash scripts/deploy.sh
bash scripts/healthcheck.sh
python3 -B scripts/operations.py status
```

No se ejecutó SSH ni se creó ningún directorio en servidor. El operador elegirá
la ubicación de checkout; no se presupone /opt ni una ruta con datos existentes.
Preflight comprueba Docker Linux, Compose/config, archivo de entorno regular,
permisos600/400 en POSIX, campos/URLs/placeholders, release/runtime, checkout limpio
y puerto libre o propiedad de esta API. No consulta Correspondencia ni biométrico.

Migrate exige --confirm-migrations. --initial-empty-db comprueba que public no
tenga tablas; no sirve para adoptar una DB existente. En una actualización, omitir
ese flag: crea backup pg_dump privado en .local/backups antes de upgrade head.
Detiene API/worker y los deja detenidos si algo falla; deploy vuelve a levantarlos
solo después del chequeo de head. Migración aprobada inicial: 0001_rrhh.
No se ejecuta upgrade como efecto secundario de build/up/deploy.

Deploy exige release validated y árbol limpio; build, PostgreSQL healthy, esquema
correcto, up API/worker y healthcheck. No altera bases ajenas ni inicia túneles.
Health confirma /health200, estados healthy de los tres servicios y Alembic head.
La sonda worker comprueba proceso/DB/esquema; consumo de trabajos se valida además
con un fixture local. No prueba por sí sola acceso a servicios externos.

## Rollback

Guardar el SHA DEPLOY aprobado antes de actualizar. Tras revisar compatibilidad:

```bash
bash scripts/rollback.sh --ref SHA_COMPLETO_DEPLOY_ANTERIOR --schema-compatible
```

La herramienta verifica checkout limpio y release anterior validated, hace checkout
detach del SHA aprobado, build y recrea API/worker. Nunca hace downgrade Alembic,
restore automático, down -v, prune ni elimina PostgreSQL. Si el esquema no es
compatible, detener y planificar recuperación/restore por separado. Conservar
backups fuera del repositorio y ensayar restauración antes de producción.
En un primer despliegue sin release previo, la recuperación es detener aplicación
y corregir configuración; no fingir que existe un rollback anterior.

## Cloudflare Tunnel propuesto — NO ejecutar ahora

Dominio futuro de esta API: https://rrhh.sipesipe.gob.bo.
Reutilizar el túnel municipal existente tras identificarlo y autorizar el cambio.
Insertar una entrada ANTES del catch-all, preservando todos los hostnames actuales:

```yaml
ingress:
  # Conservar entradas existentes...
  - hostname: rrhh.sipesipe.gob.bo
    service: http://127.0.0.1:8300
  - service: http_status:404
```

Si se usa túnel gestionado por archivo, comandos posteriores del operador:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel route dns ID_TUNEL_EXISTENTE_APROBADO rrhh.sipesipe.gob.bo
# Recargar el servicio existente solo tras aprobar el config completo.
```

Si está gestionado desde dashboard, crear el Public Hostname equivalente allí y
no mezclar ambos métodos. No crear un túnel nuevo a ciegas ni abrir puertos del
router. No configurar noTLSVerify. TLS público termina en Cloudflare; origin es
HTTP solo loopback. No se ha verificado el túnel/config/puerto remoto porque no
se accedió al servidor. Este repo publica API; el hosting Flutter es una fase aparte.

## Validación local de esta entrega

Validación operativa y Compose en PC con configuración aleatoria exclusivamente
de prueba, upstreams .invalid.test y proyecto rrhh-api-deploy-validation-20261116.
Resultado definitivo de build, migración, health, worker, reinicio/persistencia y
tests registrado al cerrar la entrega. No usar esos valores como configuración QA.
Los artefactos locales quedan ignorados y el volumen de prueba es independiente.

## Riesgos pendientes para QA

Proveer secretos y URL maestra reales; verificar permisos efectivos MANAGER,
refresh/cookies/CORS en origen final y conectividad saliente HTTPS. Definir M2M si
se requiere. Aprobar reglas contractuales/calendarios/turnos/parámetros/políticas
pendientes; no cargar fixtures como reglas institucionales. Detener el sincronizador
anterior antes de habilitar uno nuevo: el lock solo coordina la misma DB RRHH.
Biometric API sigue leyendo histórico físico antes de filtrar/paginar; reportes
persistidos no dependen de ese servicio, pero sí del maestro para auth/cabecera.
Verificar puerto8300 real en Debian y túnel existente antes de tocar configuración.
