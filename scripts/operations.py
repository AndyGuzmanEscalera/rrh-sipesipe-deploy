#!/usr/bin/env python3
"""Explicit RRHH operations, adapted from the municipal biometric deploy pattern."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
PROJECT='rrhh-api-deploy'
MANIFEST_KEYS={'release','source_sha','source_branch','build_date','status','notes'}
HEAD='0002_biometric_import_batches'


class Refusal(RuntimeError): pass


def require(ok,message):
    if not ok: raise Refusal(message)


def manifest(root=ROOT):
    data=json.loads((Path(root)/'release.json').read_text(encoding='utf-8'))
    require(set(data)==MANIFEST_KEYS,'Invalid release.json keys')
    require(re.fullmatch('[0-9a-f]{40}',data['source_sha']),'Invalid source SHA')
    require(data['source_branch']=='develop','Source branch must be develop')
    require(data['release']=='qa-'+data['source_sha'][:12],'Release/SHA mismatch')
    require(data['status'] in {'prepared','validated'} and data['notes'].strip(),'Release not prepared')
    datetime.fromisoformat(data['build_date'])
    return data


def dotenv(path):
    path=Path(path)
    require(path.is_file() and not path.is_symlink(),'Private regular env file required')
    if os.name=='posix': require(path.stat().st_mode & 0o077==0,'Env file requires permissions 600/400')
    values={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key,sep,value=line.partition('=')
        require(sep and re.fullmatch('[A-Z][A-Z0-9_]*',key),'Invalid env key')
        require(key not in values and value==value.strip() and not any(x in value for x in '\x00$`\"\''),'Use literal unquoted env values without expansion')
        values[key]=value
    return values


def validate_env(v):
    required=['APP_ENV','POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD','CORRESPONDENCIA_API_URL',
        'BIOMETRIC_API_URL','BIOMETRIC_API_TOKEN','BIOMETRIC_DEVICE_ID','LOCAL_TIMEZONE',
        'SESSION_COOKIE_NAME','SESSION_COOKIE_PATH','SESSION_COOKIE_SECURE','SESSION_COOKIE_SAMESITE']
    for key in required: require(v.get(key),'Missing variable: '+key)
    for key in required+['CORRESPONDENCIA_API_TOKEN']:
        value=v.get(key,'')
        require(not any(word in value.upper() for word in ('REPLACE','CAMBIAR','CHANGEME','YOUR_TOKEN','PLACEHOLDER')),'Unfilled placeholder: '+key)
    require(v['APP_ENV'] in {'production','qa','development'},'Invalid APP_ENV')
    require(re.fullmatch('[0-9a-fA-F]{64,}',v['POSTGRES_PASSWORD']),'Use a private PostgreSQL password of at least 32 random hex bytes')
    for key in ('POSTGRES_USER','POSTGRES_DB'):
        require(re.fullmatch('[a-z][a-z0-9_]{0,62}',v[key]),'Invalid '+key)
    for key in ('CORRESPONDENCIA_API_URL','BIOMETRIC_API_URL'):
        u=urlsplit(v[key])
        qa_private=(key=='CORRESPONDENCIA_API_URL' and v['APP_ENV']=='qa'
            and v.get('ALLOW_PRIVATE_HTTP_CORRESPONDENCIA','false')=='true'
            and v[key] in {'http://backend:8000','http://backend:8000/'})
        require(qa_private or (u.scheme=='https' and u.hostname and not u.username and not u.password
            and not u.query and not u.fragment),'HTTPS required except explicit QA backend:8000: '+key)
    require(v.get('ALLOW_PRIVATE_HTTP_CORRESPONDENCIA','false') in {'true','false'},'Invalid private HTTP flag')
    require(len(v['BIOMETRIC_API_TOKEN'])>=32,'Biometric token too short')
    for key in ('CORRESPONDENCIA_API_TIMEOUT','BIOMETRIC_API_TIMEOUT'):
        require(re.fullmatch(r'\d+(\.\d+)?',v.get(key,'')) and 0<float(v[key])<=600,'Invalid '+key)
    port=v.get('RRHH_API_PORT','8300')
    require(port.isdigit() and 1024<=int(port)<=65535,'Invalid RRHH_API_PORT')
    require(v['SESSION_COOKIE_SECURE']=='true','Public deployment requires Secure cookies')
    require(v['SESSION_COOKIE_SAMESITE'] in {'lax','strict','none'},'Invalid SameSite')
    require(v['SESSION_COOKIE_PATH'].startswith('/') and not any(x.isspace() for x in v['SESSION_COOKIE_PATH']),'Invalid cookie path')
    origins=[x.strip() for x in v.get('CORS_ALLOW_ORIGINS','').split(',') if x.strip()]
    require(all(urlsplit(x).scheme=='https' and urlsplit(x).netloc and '*' not in x for x in origins),'CORS requires exact HTTPS origins')
    require(v.get('SCHEDULER_ENABLED')=='false','Scheduler must stay disabled until approved cutover')
    return v


class Context:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve()
        self.m=manifest(self.root)
        self.envfile=Path(os.environ.get('RRHH_ENV_FILE',self.root/'.env')).resolve()
        self.v=validate_env(dotenv(self.envfile))
        self.project=os.environ.get('RRHH_COMPOSE_PROJECT',PROJECT)
        require(re.fullmatch('rrhh-api-deploy(?:-[a-z0-9-]+)?',self.project),'Invalid dedicated Compose project')
        self.env={**os.environ,**self.v,'RUNTIME_ENV_FILE':str(self.envfile),
            'SOURCE_SHA':self.m['source_sha'],'RELEASE_VERSION':self.m['release']}
        self.base=['docker','compose','--project-directory',str(self.root),'--project-name',self.project,
            '--env-file',str(self.envfile),'-f',str(self.root/'docker-compose.yml')]

    def run(self,args,timeout=300,output=None,label='Operation'):
        try:
            p=subprocess.run(args,cwd=self.root,env=self.env,stdout=subprocess.PIPE if output is None else output,
                stderr=subprocess.PIPE,timeout=timeout)
        except (OSError,subprocess.TimeoutExpired): raise Refusal(label+' unavailable or timed out; inspect state privately') from None
        require(p.returncode==0,label+' failed; command output withheld to protect credentials')
        return p.stdout.decode('utf-8') if output is None else ''

    def dc(self,*args,**kwargs): return self.run([*self.base,*args],**kwargs)

    @property
    def port(self): return int(self.v.get('RRHH_API_PORT','8300'))


def clean(c):
    require(not c.run(['git','status','--porcelain']).strip(),'Deploy checkout must be clean')


def preflight(c):
    require(c.run(['docker','info','--format','{{.OSType}}']).strip()=='linux','Docker Linux required')
    c.run(['docker','compose','version'])
    c.dc('config','--quiet',label='Compose config (requires Compose >=2.30)')
    c.run(['docker','network','inspect','correspondencia-deploy_edge'],label='Existing Correspondencia edge network')
    for name in ['backend/Dockerfile','backend/.dockerignore','backend/requirements.txt','backend/app/main.py',
        'backend/alembic.ini','backend/alembic/versions/0001_rrhh_initial_rrhh_owned_tables.py',
        'backend/alembic/versions/0002_biometric_import_batches.py']:
        path=c.root/name
        require(path.is_file() and not path.is_symlink(),'Missing/linked runtime: '+name)
    clean(c)
    owners=c.run(['docker','ps','--filter','publish='+str(c.port),'--format','{{.ID}}']).split()
    ours=c.dc('ps','-q','api').split()
    require(all(any(a.startswith(o) for a in ours) for o in owners),'Port owned by another container')
    if not owners:
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1',c.port))
            except OSError: raise Refusal('Local port unavailable') from None
    print('Preflight: Docker/Compose/env/release/runtime/port OK; no external API calls')


def schema(c):
    return c.dc('exec','-T','postgres','psql','-U',c.v['POSTGRES_USER'],'-d',c.v['POSTGRES_DB'],
        '-Atc','select version_num from alembic_version').strip()


def health(c):
    try:
        with urlopen('http://127.0.0.1:'+str(c.port)+'/health',timeout=10) as response:
            data=json.load(response)
            require(response.status==200 and data.get('status')=='ok','API health failed')
    except OSError: raise Refusal('API health unavailable') from None
    for service in ('api','worker','postgres'):
        cid=c.dc('ps','-q',service).strip()
        require(cid,'Service missing: '+service)
        state=json.loads(c.run(['docker','inspect','--format','{{json .State}}',cid]))
        require(state['Running'] and state.get('Health',{}).get('Status')=='healthy','Service not healthy: '+service)
    require(schema(c)==HEAD,'Unexpected database migration')
    print('Health: API200, API/worker/PostgreSQL healthy, schema '+HEAD)


def backup(c):
    directory=c.root/'.local/backups';directory.mkdir(parents=True,exist_ok=True)
    name=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.dump')
    with name.open('xb') as stream:
        os.chmod(name,0o600)
        c.dc('exec','-T','postgres','pg_dump','-U',c.v['POSTGRES_USER'],'-d',c.v['POSTGRES_DB'],
            '--format=c',output=stream,label='PostgreSQL backup')
    require(name.stat().st_size>0,'Empty backup')
    print('Private database backup completed')


def migrate(c,args):
    require(args.confirm_migrations,'Migrations require --confirm-migrations')
    preflight(c)
    c.dc('build',timeout=3600,label='Runtime build')
    c.dc('up','-d','--wait','postgres')
    count=c.dc('exec','-T','postgres','psql','-U',c.v['POSTGRES_USER'],'-d',c.v['POSTGRES_DB'],
        '-Atc',"select count(*) from information_schema.tables where table_schema='public'").strip()
    if args.initial_empty_db: require(count=='0','--initial-empty-db requires an empty dedicated database')
    # Freeze writers before the backup and schema changes; stay stopped on failure.
    c.dc('stop','api','worker')
    if not args.initial_empty_db: backup(c)
    c.dc('run','--rm','--no-deps','api','python','-m','alembic','upgrade','head',label='Explicit Alembic upgrade')
    require(schema(c)==HEAD,'Migration head mismatch')
    print('Migration complete; application remains stopped. Run deploy explicitly.')


def deploy(c):
    require(c.m['status']=='validated','Release requires local validation')
    preflight(c)
    c.dc('build',timeout=3600,label='Runtime build')
    c.dc('up','-d','--wait','postgres')
    require(schema(c)==HEAD,'Run the explicit migration command before deploy')
    c.dc('up','-d','--no-deps','--wait','--wait-timeout','180','api','worker')
    health(c)


def rollback(c,args):
    require(args.schema_compatible,'Application rollback requires --schema-compatible after review')
    require(re.fullmatch('[0-9a-f]{40}',args.ref or ''),'Use full approved previous deploy SHA')
    require(args.ref!=c.run(['git','rev-parse','HEAD']).strip(),'Rollback target equals HEAD')
    preflight(c)
    previous=json.loads(c.run(['git','show',args.ref+':release.json']))
    require(previous.get('status')=='validated' and previous.get('source_branch')=='develop','Target release not validated')
    require(schema(c)==HEAD,'Review database compatibility before application rollback')
    c.run(['git','checkout','--detach',args.ref],label='Approved application rollback checkout')
    deploy(Context(c.root))
    print('Application rollback only; no database downgrade or volume removal')


@contextmanager
def operation_lock(c):
    with (c.root/'.operation.lock').open('a+b') as stream:
        if os.name=='posix':
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        else:
            import msvcrt
            stream.write(b'0');stream.flush();stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        try: yield
        finally:
            if os.name=='posix': fcntl.flock(stream,fcntl.LOCK_UN)
            else:
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['preflight','migrate','deploy','healthcheck','rollback','backup','status','version'])
    parser.add_argument('--confirm-migrations',action='store_true')
    parser.add_argument('--initial-empty-db',action='store_true')
    parser.add_argument('--schema-compatible',action='store_true')
    parser.add_argument('--ref')
    args=parser.parse_args();os.umask(0o077);c=Context()
    if args.command=='preflight': preflight(c)
    elif args.command=='healthcheck': health(c)
    elif args.command=='version': print(json.dumps(c.m,indent=2))
    elif args.command=='status': print(c.dc('ps'))
    else:
        with operation_lock(c):
            if args.command=='deploy': deploy(c)
            elif args.command=='migrate': migrate(c,args)
            elif args.command=='backup': backup(c)
            else: rollback(c,args)


if __name__=='__main__':
    try: main()
    except (Refusal,OSError,ValueError):
        # Refusal text is ours; never echo arbitrary JSON/env/subprocess values.
        error=sys.exc_info()[1]
        print('STOP: '+(str(error) if isinstance(error,Refusal) else 'Invalid file/configuration or operation lock unavailable'),file=sys.stderr)
        sys.exit(1)
