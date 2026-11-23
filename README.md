# rrh-sipesipe-deploy — API RRHH

Deploy exclusivo de la API RRHH independiente. GitHub:
`https://github.com/AndyGuzmanEscalera/rrh-sipesipe-deploy`.
Ruta de preparación local: `C:\dev\rrhh_sipe_sipe_api_deploy`.
No incluye Flutter ni maestros de Correspondencia.

QA server-to-server: api/worker conservan edge/data y se conectan también a la
red externa existente `correspondencia-deploy_edge`. PostgreSQL permanece solo
en data. No crear/adoptar esa red ni modificar los contenedores de Correspondencia.
`APP_ENV=qa` + `ALLOW_PRIVATE_HTTP_CORRESPONDENCIA=true` permite únicamente
`http://backend:8000` (o la misma URL con barra final). El flag es false por defecto.
En producción HTTP se rechaza incluso con el flag; usar
`https://api-correspondencia.sipesipe.gob.bo` y flag=false. Biometric API siempre HTTPS.
Ver [comandos QA](docs/QA_CORRESPONDENCIA_INTERNA.md).

```text
rrhh-sipe-sipe-api / develop
  → scripts/prepare_deploy.py (SHA esperado y SOURCE limpio)
  → rrh-sipesipe-deploy / main
  → Debian / Docker Compose (ejecución posterior autorizada)
  → Cloudflare Tunnel / https://rrhh.sipesipe.gob.bo (pendiente)
```

Patrón auditado: Correspondencia aporta runtime backend y Alembic, aislamiento de
red/volumen y servicios endurecidos; Gestión aporta PostgreSQL/Compose y migración
operada explícitamente; Biometric API aporta release.json, prepare_deploy, scripts
shell que delegan a operations.py, preflight, healthcheck y rollback explícito.
Se conservan las claves de su manifest: release/source_sha/source_branch/build_date/
status/notes. El repo SOURCE está identificado en las notas y etiquetas OCI.

## Release de Importación Biométrica

Vea [actualización en servidor, respaldo, rollback y aceptación](docs/DEPLOY_BIOMETRIC_IMPORT.md).
Esta release usa los archivos exactos de develop mediante --exact-runtime. El acceso HTTP interno sigue reservado a promociones QA con overlay.

## Promoción en PC

Se aloja el preparador en DEPLOY para conservar intacto el SOURCE aprobado
`f5affb69a1777289ab6f4fd8d27ae259b41895d1`. No requiere copiar a mano ni hacer un
commit artificial en SOURCE. Python3.12+ y Git en PC:

```powershell
python scripts/prepare_deploy.py --source C:\dev\rrhh_sipe_sipe_api --expected-source-sha f5affb69a1777289ab6f4fd8d27ae259b41895d1 --validate-only
python scripts/prepare_deploy.py --source C:\dev\rrhh_sipe_sipe_api --expected-source-sha f5affb69a1777289ab6f4fd8d27ae259b41895d1
```

Exige rama develop, árbol completo limpio y SHA exacto. Exporta blobs Git confirmados:
app/, alembic/, alembic.ini, requirements.txt, Dockerfile y .dockerignore a backend/.
No incluye .env, claves, caches, DB, tests backend ni documentación de desarrollo.
La excepción QA es un overlay explícito de DEPLOY en
`patches/qa-correspondencia-http.patch`, sin modificar SOURCE. El preparador prueba
y reaplica ese patch en staging temporal antes de reemplazar runtime; si una
versión futura de SOURCE es incompatible, la promoción se detiene. source_sha
identifica el runtime base; el commit DEPLOY y el patch identifican la diferencia QA.
Regenera manifest con status=prepared; después de validar runtime se cambia a
validated y se registra evidencia en OPERACION.md antes de commit/push DEPLOY.
Los tests/ de este repo prueban solo promoción y herramientas operativas.

## Servicios y puerto

API y worker comparten runtime SOURCE, PostgreSQL16 propio y volumen Compose
`rrhh-api-deploy_rrhh_postgres_data`. Nunca montar volúmenes de otro proyecto.
PostgreSQL no publica ningún puerto. API publica **127.0.0.1:8300→8000**.
Worker no publica puertos. Red data interna; API/worker tienen red edge para HTTPS
a Correspondencia/Biometric API. No hay conexiones SQL hacia Correspondencia.

Puerto8300 elegido tras auditar las configuraciones locales: Correspondencia8080,
Gestión8001, Biometric8200; el desarrollo local antiguo también usa8000. Se comprobó
8300 libre en PC. No se consultó Debian: el preflight debe verificarlo allí antes
de usarlo. RRHH_API_PORT permite corregir una colisión sin abrir la API en LAN.

## Desarrollo local en Windows

El Compose base permanece orientado a Debian/producción y conserva la red
externa `correspondencia-deploy_edge`. Para desarrollo local no crear ni conectar
redes manualmente: el override retira esa red de API/worker y consulta la
Correspondencia local expuesta por Docker Desktop mediante
`host.docker.internal:8000`.

```powershell
Copy-Item .env.local.example .env.local
docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml up -d
docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml ps
curl http://127.0.0.1:8300/health
```

`.env.local` está ignorado y no contiene secretos reales. Este flujo no usa los
scripts operativos de Debian ni crea/conecta `correspondencia-deploy_edge`.
Para Flutter local:

```powershell
flutter run -d chrome --dart-define=API_BASE_URL=http://127.0.0.1:8300
```

El HTTP privado de Correspondencia se permite únicamente con
`APP_ENV=development`, `ALLOW_PRIVATE_HTTP_CORRESPONDENCIA=true` y la URL exacta
`http://host.docker.internal:8000`; producción sigue exigiendo HTTPS.

## Operación posterior en Debian

Requiere Python3, Git, Docker Linux y Compose>=2.30 (env_file raw). No ejecutar hasta
la siguiente autorización. Ver los comandos completos en [OPERACION.md](docs/OPERACION.md).

1. Clonar main, preparar .env privado con permisos600 y valores reales revisados.
2. Preflight (no llama a APIs externas).
3. Migración inicial explícita con --confirm-migrations --initial-empty-db.
4. Deploy y healthcheck explícitos.
5. Autorizar/verificar integración QA; solo después configurar túnel/DNS.

Deploy no ejecuta migraciones implícitas. La operación migrate crea backup privado
antes de migrar una DB existente; el modo inicial se niega si la DB no está vacía.
No existe comando para downgrade ni para borrar volúmenes. Rollback de aplicación
requiere SHA anterior aprobado y revisión explícita de compatibilidad de esquema.
En el primer release todavía no existe un release anterior para rollback.

Auth conserva identidad/sesión de Correspondencia: MANAGER activo entra en RRHH,
sin exigir rrhh.read; administración requiere rrhh.manage, force protección extra.
No hay usuarios, JWT, refresh ni passwords propios RRHH.
La cookie RRHH solo transporta el refresh opaco original del maestro.

**No se desplegó al servidor, configuró secretos reales, creó túnel ni DNS.**
