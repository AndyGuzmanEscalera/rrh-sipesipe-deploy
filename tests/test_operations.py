import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
ops=load('operations');promote=load('prepare_deploy')


class EnvironmentTests(unittest.TestCase):
    def valid(self):
        with tempfile.TemporaryDirectory() as directory:
            private=Path(directory)/'runtime.env'
            private.write_bytes((ROOT/'.env.example').read_bytes());private.chmod(0o600)
            value=ops.dotenv(private)
        value.update(POSTGRES_PASSWORD='a'*64,BIOMETRIC_API_TOKEN='b'*64,
            CORRESPONDENCIA_API_URL='https://masters.invalid.test')
        return value

    def test_valid_private_configuration(self): ops.validate_env(self.valid())

    def test_placeholders_secrets_urls_cors_and_scheduler_rejected(self):
        for key,value in [('POSTGRES_PASSWORD','password'),('BIOMETRIC_API_TOKEN','CAMBIAR_EN_SERVIDOR'),
            ('CORRESPONDENCIA_API_URL','http://masters.test'),('BIOMETRIC_API_URL','https://user:password@host'),
            ('CORS_ALLOW_ORIGINS','*'),('SESSION_COOKIE_SECURE','false'),('RRHH_API_PORT','80'),
            ('SCHEDULER_ENABLED','true')]:
            with self.subTest(key=key),self.assertRaises(ops.Refusal):
                ops.validate_env({**self.valid(),key:value})

    def test_env_duplicate_and_expansion_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.env'
            for text in ['KEY=x\nKEY=y\n','KEY=$SECRET\n','KEY="quoted"\n']:
                path.write_text(text);path.chmod(0o600)
                with self.assertRaises(ops.Refusal): ops.dotenv(path)

    @unittest.skipUnless(os.name=='posix','POSIX permission semantics')
    def test_world_readable_env_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.env';path.write_text('KEY=x\n');path.chmod(0o644)
            with self.assertRaises(ops.Refusal): ops.dotenv(path)

    def test_malformed_release_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'release.json';value=json.loads((ROOT/'release.json').read_text())
            for key,bad in [('source_branch','main'),('source_sha','short'),('release','other'),('status','unknown')]:
                path.write_text(json.dumps({**value,key:bad}))
                with self.assertRaises(ops.Refusal): ops.manifest(directory)

    def test_failed_command_does_not_echo_credentials(self):
        c=object.__new__(ops.Context);c.env={};c.root=ROOT
        with patch.object(ops.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr=b'Authorization: PRIVATE',stdout=b'')):
            with self.assertRaises(ops.Refusal) as error: c.run(['dummy'])
        self.assertNotIn('PRIVATE',str(error.exception))

    def test_migration_requires_explicit_confirmation(self):
        with self.assertRaises(ops.Refusal): ops.migrate(None,SimpleNamespace(confirm_migrations=False))

    def test_rollback_requires_compatibility_acknowledgement(self):
        with self.assertRaises(ops.Refusal): ops.rollback(None,SimpleNamespace(schema_compatible=False))


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.source=Path(self.temp.name)/'source';self.source.mkdir()
        self.target=Path(self.temp.name)/'deploy';self.target.mkdir();(self.target/'.git').mkdir()
        self.git('init','--initial-branch=develop')
        self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        for n in range(40):
            path=self.source/'app'/('fixture'+str(n)+'.py');path.parent.mkdir(exist_ok=True);path.write_text('value=1\n')
        config=self.source/'app/core/config.py';config.parent.mkdir();config.write_text('value=1\n')
        path=self.source/'alembic/versions/0001_rrhh_initial_rrhh_owned_tables.py'
        path.parent.mkdir(parents=True);path.write_text('revision="0001_rrhh"\n')
        for name in ['requirements.txt','alembic.ini','Dockerfile','.dockerignore']:
            (self.source/name).write_text('fixture\n')
        (self.source/'.gitignore').write_text('.env\n')
        self.git('add','app','alembic','requirements.txt','alembic.ini','Dockerfile','.dockerignore','.gitignore')
        self.git('commit','-m','fixture');self.sha=self.git('rev-parse','HEAD').strip()
        self.override=patch.object(promote,'ROOT',self.target);self.override.start();self.addCleanup(self.override.stop)

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.source),*args],stderr=subprocess.PIPE).decode()

    def test_exact_committed_runtime_without_local_env(self):
        (self.source/'.env').write_text('PRIVATE=not-for-copy\n')
        promote.prepare(self.source,self.sha)
        self.assertFalse((self.target/'backend/.env').exists())
        self.assertEqual(json.loads((self.target/'release.json').read_text())['source_sha'],self.sha)
        self.assertEqual((self.target/'backend/app/fixture0.py').read_bytes(),self.git('show','HEAD:app/fixture0.py').encode())

    def test_dirty_source_refused_before_any_copy(self):
        (self.source/'untracked.txt').write_text('pending')
        with self.assertRaises(AssertionError): promote.prepare(self.source,self.sha)
        self.assertFalse((self.target/'backend').exists())

    def test_wrong_branch_or_sha_refused(self):
        with self.assertRaises(AssertionError): promote.prepare(self.source,'0'*40)
        self.git('branch','-m','main')
        with self.assertRaises(AssertionError): promote.prepare(self.source,self.sha)

    def test_validation_only_does_not_mutate_target(self):
        promote.prepare(self.source,self.sha,True)
        self.assertFalse((self.target/'backend').exists())

    def test_explicit_deploy_overlay_survives_promotion(self):
        directory=self.target/'patches';directory.mkdir()
        (directory/'qa-correspondencia-http.patch').write_text(
            'diff --git a/backend/app/core/config.py b/backend/app/core/config.py\n'
            '--- a/backend/app/core/config.py\n+++ b/backend/app/core/config.py\n'
            '@@ -1 +1 @@\n-value=1\n+value=2\n')
        promote.prepare(self.source,self.sha)
        self.assertEqual((self.target/'backend/app/core/config.py').read_text(),'value=2\n')
        self.assertEqual(self.git('show','HEAD:app/core/config.py'),'value=1\n')


if __name__=='__main__': unittest.main()
