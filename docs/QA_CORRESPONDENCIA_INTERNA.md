# Conexión interna de Correspondencia para QA

Solo cambia este deploy RRHH. No modifica SOURCE RRHH, Correspondencia, sus redes,
contenedores, túneles ni DNS. No se usa la IP 172.30.237.11. El servicio backend
se resuelve por DNS Docker en la red externa existente correspondencia-deploy_edge.
API y worker mantienen edge/data y agregan correspondencia_edge; PostgreSQL solo data.

## Política

| Configuración | Resultado |
|---|---|
| production + HTTP, con o sin flag | rechazado |
| production + HTTPS sin credenciales en URL | permitido |
| qa + flag=true + http://backend:8000 | permitido |
| qa + flag=false + HTTP | rechazado |
| qa + HTTP público / IP / otro puerto / path / userinfo / query | rechazado |
| Biometric API + HTTP en cualquier entorno | rechazado |

ALLOW_PRIVATE_HTTP_CORRESPONDENCIA=false es el valor por defecto. Se exige qa
exactamente; no basta con que el entorno sea distinto de production. Se conserva
TLS verificado para HTTPS y redirects desactivados; no se relaja verify=False.
El tráfico HTTP interno QA incluye identidad/refresh y solo es aceptable en esta
red de confianza del host. No sustituye el HTTPS definitivo de producción:
https://api-correspondencia.sipesipe.gob.bo.

El cambio runtime se mantiene como patch versionado DEPLOY aplicado tras exportar
la base SOURCE en prepare_deploy.py. El SHA fuente no se atribuye a esta corrección
operativa; el commit DEPLOY y el patch son la trazabilidad adicional. Si el patch
ya está en SOURCE o deja de aplicar, la promoción falla y requiere reconciliación
explícita; no borra silenciosamente la excepción QA.

## Comandos posteriores en Debian (NO ejecutados desde el PC)

Desde el checkout RRHH existente; no clonar/levantar un segundo proyecto ni cambiar
RRHH_COMPOSE_PROJECT respecto al despliegue existente. No repetir migraciones para
este cambio de red/configuración. Mantener el scheduler desactivado.

```bash
git pull --ff-only origin main
python3 - <<'PY'
import os
from pathlib import Path
p=Path(os.environ.get('RRHH_ENV_FILE','.env'))
assert p.is_file() and not p.is_symlink(), 'Se requiere el entorno privado existente'
updates={'APP_ENV':'qa','CORRESPONDENCIA_API_URL':'http://backend:8000',
         'ALLOW_PRIVATE_HTTP_CORRESPONDENCIA':'true'}
lines=p.read_text().splitlines();seen=set();result=[]
for line in lines:
    key=line.partition('=')[0]
    if key in updates:
        line=key+'='+updates[key];seen.add(key)
    result.append(line)
result.extend(k+'='+v for k,v in updates.items() if k not in seen)
p.write_text('\n'.join(result)+'\n');p.chmod(0o600)
print('Configuración QA actualizada; secretos existentes no impresos')
PY
bash scripts/preflight.sh
bash scripts/deploy.sh
bash scripts/healthcheck.sh
```

El preflight exige red externa existente y despliegue limpio. No ejecutar docker
network create/connect sobre Correspondencia para sortear un error. Si se expone
el puerto correcto pero no hay red, verificar el diagnóstico antes de proceder.

Verificar redes y HTTP interno desde los dos procesos RRHH, sin mostrar variables
de entorno completas ni tokens:

```bash
python3 - <<'PY'
import json
from scripts.operations import Context
c=Context()
for service in ('api','worker','postgres'):
    cid=c.dc('ps','-q',service).strip()
    networks=json.loads(c.run(['docker','inspect','--format',
                              '{{json .NetworkSettings.Networks}}',cid]))
    print(service,sorted(networks))
    assert ('correspondencia-deploy_edge' in networks)==(service!='postgres')
    if service!='postgres':
        probe="from app.core.config import get_settings; import urllib.request; s=get_settings(); assert s.app_env=='qa' and s.allow_private_http_correspondencia and s.correspondencia_api_url=='http://backend:8000'; r=urllib.request.urlopen(s.correspondencia_api_url+'/health',timeout=10); assert r.status==200; print('http://backend:8000/health ->',r.status)"
        print(c.dc('exec','-T',service,'python','-c',probe).strip())
PY
```

Repetir login delegado por el HTTPS público RRHH existente. La contraseña se pide
de forma oculta y el token solo vive en memoria; nunca imprimir JSON de login:

```bash
python3 -c 'import json,getpass,http.cookiejar,urllib.request; base="https://rrhh.sipesipe.gob.bo"; c=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())); data=json.dumps({"username":input("Usuario Correspondencia: "),"password":getpass.getpass("Contraseña: ")}).encode(); r=c.open(urllib.request.Request(base+"/auth/login",data=data,headers={"Content-Type":"application/json"}),timeout=45); token=json.load(r)["access_token"]; print("login:",r.status); me=c.open(urllib.request.Request(base+"/auth/me",headers={"Authorization":"Bearer "+token}),timeout=45); print("me:",me.status,"roles:",json.load(me)["roles"]); refresh=c.open(urllib.request.Request(base+"/auth/refresh",data=b"",method="POST"),timeout=45); assert "access_token" in json.load(refresh); print("refresh:",refresh.status)'
```

No se cambian cookie Secure/HttpOnly ni los permisos: MANAGER activo entra;
rrhh.manage sigue siendo adicional y force resync conserva su protección.

## Validación local

La red externa del Debian no existe en el PC. Se prueba con override de red externa
descartable de otro nombre, un backend fixture y DB RRHH propias, sin conectar los
contenedores existentes de Correspondencia/Gestión ni leer cuentas reales.
Se verifica API/worker healthy, DNS backend, /health200 desde ambos, mismo URL,
login/me/refresh200 y token inválido401 con cookies Secure/HttpOnly intactas.
Las matrices de preflight/Pydantic cubren producción/QA, flag default, HTTP público
y variantes de bypass. También se verifica que la promoción reaplique el overlay.
El resultado real contra Correspondencia en Debian queda pendiente de ejecutar los
comandos anteriores; aquí no se realizó SSH, deploy remoto, DNS ni túneles.
