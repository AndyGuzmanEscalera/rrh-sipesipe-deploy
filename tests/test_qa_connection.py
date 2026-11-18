"""QA exception is identical at preflight and runtime; never a general HTTP bypass."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'backend'))
from operations import Context,Refusal,validate_env
from prepare_deploy import apply_deploy_overlays
from pydantic import ValidationError

CASES=[
    ('production',False,'http://backend:8000',False),
    ('production',True,'http://backend:8000',False),
    ('production',False,'https://api-correspondencia.sipesipe.gob.bo',True),
    ('qa',True,'http://backend:8000',True),
    ('qa',True,'http://backend:8000/',True),
    ('qa',False,'http://backend:8000',False),
    ('qa',True,'https://api-correspondencia.sipesipe.gob.bo',True),
    ('qa',True,'http://public.example.com',False),
    ('qa',True,'http://172.30.237.11:8000',False),
    ('qa',True,'http://backend:80',False),
    ('qa',True,'http://backend:8000/api',False),
    ('qa',True,'http://backend:8000?next=public',False),
    ('qa',True,'http://backend:8000#fragment',False),
    ('qa',True,'http://user:password@backend:8000',False),
    ('qa',True,'http://backend.evil.example:8000',False),
    ('development',True,'http://backend:8000',False),
]


class QaConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.qa_runtime = Path(directory.name) / 'backend'
        shutil.copytree(ROOT / 'backend' / 'app', cls.qa_runtime / 'app',
                        ignore=shutil.ignore_patterns('__pycache__'))
        target = cls.qa_runtime / 'app/core/config.py'
        current = target.read_bytes()
        # A promoted QA checkout may already include the overlay.
        if b'allow_private_http_correspondencia' not in current:
            target.write_bytes(apply_deploy_overlays({'app/core/config.py': current})['app/core/config.py'])
        spec = importlib.util.spec_from_file_location('qa_fixture_config', target)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        cls.Settings = config.Settings

    def env(self,app_env='qa',allow=True,url='http://backend:8000'):
        values={}
        for line in (ROOT/'.env.example').read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                key,value=line.split('=',1);values[key]=value
        values.update(APP_ENV=app_env,ALLOW_PRIVATE_HTTP_CORRESPONDENCIA=str(allow).lower(),
            POSTGRES_PASSWORD='a'*64,BIOMETRIC_API_TOKEN='b'*64,CORRESPONDENCIA_API_URL=url)
        return values

    def test_preflight_security_matrix(self):
        for app_env,allow,url,valid in CASES:
            with self.subTest(env=app_env,allow=allow,url=url):
                values=self.env(app_env,allow,url)
                if valid: validate_env(values)
                else:
                    with self.assertRaises(Refusal): validate_env(values)

    def test_pydantic_security_matrix(self):
        for app_env,allow,url,valid in CASES:
            with self.subTest(env=app_env,allow=allow,url=url):
                values=dict(_env_file=None,app_env=app_env,allow_private_http_correspondencia=allow,
                    correspondencia_api_url=url,biometric_api_url='https://biometric.invalid.test')
                if valid: self.assertEqual(self.Settings(**values).correspondencia_api_url,url.rstrip('/'))
                else:
                    with self.assertRaises(ValidationError): self.Settings(**values)

    def test_private_http_is_disabled_by_default(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(self.Settings(_env_file=None).allow_private_http_correspondencia)
            with self.assertRaises(ValidationError):
                self.Settings(_env_file=None,app_env='qa',correspondencia_api_url='http://backend:8000')
        values=self.env();values.pop('ALLOW_PRIVATE_HTTP_CORRESPONDENCIA')
        with self.assertRaises(Refusal): validate_env(values)

    def test_biometric_never_inherits_http_exception(self):
        values=self.env();values['BIOMETRIC_API_URL']='http://backend:8000'
        with self.assertRaises(Refusal): validate_env(values)
        with self.assertRaises(ValidationError):
            self.Settings(_env_file=None,app_env='qa',allow_private_http_correspondencia=True,
                correspondencia_api_url='http://backend:8000',biometric_api_url='http://backend:8000')

    def test_application_starts_with_internal_qa_url(self):
        child={**os.environ,**self.env(), 'PYTHONPATH':str(ROOT/'backend'),
            'PYTHONDONTWRITEBYTECODE':'1','CORRESPONDENCIA_API_TOKEN':''}
        code=f"import sys; sys.path.insert(0,{str(self.qa_runtime)!r}); from app.main import app; from fastapi.testclient import TestClient; c=TestClient(app); assert c.get('/health').status_code==200; assert c.get('/auth/me').status_code==401"
        result=subprocess.run([sys.executable,'-c',code],env=child,cwd=ROOT,capture_output=True)
        self.assertEqual(result.returncode,0,'QA application startup failed (output withheld)')

    def test_compose_external_network_and_shared_url(self):
        with tempfile.TemporaryDirectory() as directory:
            env=Path(directory)/'runtime.env'
            env.write_text('\n'.join(k+'='+v for k,v in self.env().items())+'\n');env.chmod(0o600)
            with patch.dict(os.environ,{'RRHH_ENV_FILE':str(env)}):
                c=Context();config=json.loads(c.dc('config','--format','json'))
        network=config['networks']['correspondencia_edge']
        self.assertTrue(network['external'])
        self.assertEqual(network['name'],'correspondencia-deploy_edge')
        for service in ('api','worker'):
            networks=config['services'][service]['networks']
            self.assertTrue({'edge','data','correspondencia_edge'}<=set(networks))
            values=config['services'][service]['environment']
            self.assertEqual(values['CORRESPONDENCIA_API_URL'],'http://backend:8000')
            self.assertEqual(values['APP_ENV'],'qa')
            self.assertEqual(values['ALLOW_PRIVATE_HTTP_CORRESPONDENCIA'],'true')
        self.assertEqual(set(config['services']['postgres']['networks']),{'data'})


if __name__=='__main__': unittest.main()
