#!/usr/bin/env python3
"""Promote committed SOURCE/develop runtime, keeping the approved source unchanged."""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SOURCE_REPO='https://github.com/AndyGuzmanEscalera/rrhh-sipe-sipe-api'
PATHS=['app','alembic','alembic.ini','requirements.txt','Dockerfile','.dockerignore']


def git(source,*args):
    return subprocess.check_output(['git','-C',str(source),*args],stderr=subprocess.PIPE)


def prepare(source,expected,validate_only=False):
    source=Path(source).resolve()
    assert source!=ROOT and (ROOT/'.git').exists(), 'Separate deploy checkout required'
    assert git(source,'branch','--show-current').decode().strip()=='develop', 'SOURCE must be develop'
    assert not git(source,'status','--porcelain').strip(), 'SOURCE must be completely clean'
    sha=git(source,'rev-parse','HEAD').decode().strip()
    assert re.fullmatch('[0-9a-f]{40}',expected) and sha==expected, 'Unexpected SOURCE SHA'
    listing=git(source,'ls-tree','-rz','HEAD','--',*PATHS).split(b'\0')
    files={}
    for entry in filter(None,listing):
        metadata,name=entry.split(b'\t',1)
        mode,kind,oid=metadata.decode().split()
        name=name.decode()
        path=PurePosixPath(name)
        assert mode=='100644' or mode=='100755', 'Links/submodules not allowed'
        assert kind=='blob' and not path.is_absolute() and '..' not in path.parts
        assert not set(path.parts)&{'tests','__pycache__','.env','.local','build'}
        assert not path.name.startswith('.env') and path.suffix not in {'.key','.pem','.pyc','.dump','.db'}
        data=git(source,'cat-file','blob',oid)
        assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data)
        files[name]=data
    assert len(files)>=40 and 'alembic/versions/0001_rrhh_initial_rrhh_owned_tables.py' in files
    if validate_only:
        print('SOURCE clean: '+sha+'; runtime files: '+str(len(files)))
        return
    # Delete obsolete runtime only within these two owned, verified directories.
    for relative in ['backend/app','backend/alembic']:
        target=ROOT/relative
        assert target.resolve().is_relative_to(ROOT.resolve())
        for p in [ROOT,ROOT/'backend',target,*target.rglob('*')]:
            assert not p.is_symlink() and not p.is_junction(), 'Linked runtime refused'
        if target.exists(): shutil.rmtree(target)
    for name,data in files.items():
        target=ROOT/'backend'/name
        assert target.resolve().is_relative_to((ROOT/'backend').resolve())
        assert not target.is_symlink() and not target.is_junction()
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    manifest=dict(release='qa-'+sha[:12],source_sha=sha,source_branch='develop',
        build_date=datetime.now(timezone.utc).isoformat(),status='prepared',
        notes='Runtime from '+SOURCE_REPO+'; Alembic 0001_rrhh; local validation required; no server deployment.')
    (ROOT/'release.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    assert git(source,'rev-parse','HEAD').decode().strip()==sha
    assert not git(source,'status','--porcelain').strip()
    print('Prepared '+sha+'; '+str(len(files))+' runtime files; no commit/push/deployment')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--expected-source-sha',required=True)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    try: prepare(args.source,args.expected_source_sha,args.validate_only)
    except (AssertionError,OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit('Promotion refused: '+(str(exc) if isinstance(exc,AssertionError) else 'Git/filesystem operation failed'))
