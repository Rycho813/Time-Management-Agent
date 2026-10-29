import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/source_release.py'
spec = importlib.util.spec_from_file_location('source_release', SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def repo(path, name):
    path.mkdir()
    (path/'tracked').write_text(name)
    (path/'deploy').mkdir()
    (path/'deploy/Dockerfile').write_text('FROM synthetic-only')
    env = dict(os.environ, GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    for args in [('init','-q'),('add','.'),('commit','-qm','synthetic fixture')]:
        subprocess.run(['git','-C',str(path),*args],env=env,check=True,capture_output=True)
    return release.git(path,'rev-parse','HEAD')


class SourceDeployment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.agent=self.base/'agent';self.front=self.base/'frontend'
        self.a=repo(self.agent,'agent');self.f=repo(self.front,'frontend')
        self.record={'format':1,'agent_dir':str(self.agent),'frontend_dir':str(self.front),
                     'agent_commit':self.a,'frontend_commit':self.f,
                     'image_tag':release.image_tag(self.a,self.f),'image_id':'sha256:'+'a'*64}
        self.image={'Id':self.record['image_id'],'Architecture':'amd64','Os':'linux','Size':123,
                    'Config':{'User':'10001:10001','Labels':{'org.opencontainers.image.revision':self.a,'timemap.frontend.revision':self.f}}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_fixed_checkout_rejects_drift_dirty_and_nested_paths(self):
        self.assertEqual(release.checked_repo(self.agent,self.a),self.agent)
        for path,sha in [(self.agent,'0'*40),(self.agent,self.a[:8]),(self.agent/'deploy',self.a)]:
            with self.assertRaises(ValueError):release.checked_repo(path,sha)
        (self.agent/'unexpected').write_text('synthetic')
        with self.assertRaises(ValueError):release.checked_repo(self.agent,self.a)

    def test_image_identity_rejects_retag_and_wrong_source_labels(self):
        with patch.object(release,'docker',side_effect=lambda *a:json.dumps([self.image])):
            release.image_check(self.record)
            with self.assertRaises(ValueError):release.image_check({**self.record,'image_id':'sha256:'+'b'*64})
            self.image['Config']['Labels']['timemap.frontend.revision']='0'*40
            with self.assertRaises(ValueError):release.image_check(self.record)

    def test_build_records_both_commits_and_refuses_existing_image(self):
        output=self.base/'image.json'
        args=argparse.Namespace(agent_sha=self.a,frontend_sha=self.f,frontend=str(self.front),record=str(output),ca_bundle=None)
        def docker(*args,**kwargs):
            if args[:2]==('image','ls'):return ''
            if args[0]=='build':return None
            if args[:2]==('image','inspect'):return json.dumps([self.image])
            raise AssertionError(args)
        with patch.object(release,'AGENT',self.agent),patch.object(release,'docker',side_effect=docker):
            release.build(args)
        data=json.loads(output.read_text())
        self.assertEqual(data['agent_commit'],self.a);self.assertEqual(data['frontend_commit'],self.f)
        output.unlink()
        with patch.object(release,'AGENT',self.agent),patch.object(release,'docker',return_value='existing'):
            with self.assertRaises(ValueError):release.build(args)
        self.assertFalse(output.exists())

    def test_source_changed_during_build_does_not_get_a_valid_record(self):
        output=self.base/'image.json'
        args=argparse.Namespace(agent_sha=self.a,frontend_sha=self.f,frontend=str(self.front),record=str(output),ca_bundle=None)
        def docker(*args,**kwargs):
            if args[0]=='build':(self.agent/'tracked').write_text('changed during build')
            return ''
        with patch.object(release,'AGENT',self.agent),patch.object(release,'docker',side_effect=docker):
            with self.assertRaises(ValueError):release.build(args)
        self.assertFalse(output.exists())

    def test_prepare_private_defaults_and_refuses_existing_data(self):
        record=self.base/'image.json';record.write_text(json.dumps(self.record));root=self.base/'isolated'
        args=argparse.Namespace(record=str(record),root=str(root))
        with patch.object(release,'AGENT',self.agent),patch.object(release,'verify_record'),patch.object(release.os,'geteuid',return_value=0),patch.object(release.os,'chown') as chown:
            release.prepare(args)
            self.assertEqual(chown.call_count,2)
            with self.assertRaises(ValueError):release.prepare(args)
        runtime=root/'config/runtime.env';values=dict(line.split('=',1) for line in runtime.read_text().splitlines())
        self.assertEqual(runtime.stat().st_mode&0o777,0o600)
        self.assertEqual((root/'data').stat().st_mode&0o777,0o700)
        self.assertGreaterEqual(len(values['TIMEMAP_DEVICE_TOKEN']),32)
        self.assertEqual(values['TIMEMAP_ALLOW_MODEL'],'0');self.assertEqual(values['TIMEMAP_ALLOW_SEND'],'0')
        self.assertEqual((self.agent/'deploy/runtime.env').resolve(),runtime)
        self.assertEqual((root/'current').resolve(),self.agent.parent)

    def test_compose_keeps_stop_available_but_blocks_unverified_start_and_secret_output(self):
        root=self.base/'config-root';(root/'config').mkdir(parents=True)
        (root/'config/release.json').write_text(json.dumps(self.record))
        with patch.object(release,'docker') as docker,patch.object(release,'verify_record',side_effect=ValueError('bad image')):
            release.compose(argparse.Namespace(root=str(root),compose_args=['stop','api','worker']))
            docker.assert_called_once()
            for command in [['up','-d','api'],['config'],['up','sender']]:
                with self.assertRaises(ValueError):release.compose(argparse.Namespace(root=str(root),compose_args=command))

    def test_compose_clears_remote_daemon_and_sender_profile_selectors(self):
        with patch.dict(os.environ,{'DOCKER_HOST':'tcp://example.invalid','COMPOSE_PROFILES':'send'}):
            env=release.docker_env()
        self.assertNotIn('DOCKER_HOST',env);self.assertNotIn('COMPOSE_PROFILES',env)

    def test_smoke_rejects_external_endpoint_before_reading_credentials(self):
        path=SCRIPT.with_name('smoke-test.py')
        result=subprocess.run(['python3',str(path),'--base-url','https://example.invalid','--runtime-env','/missing'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('Only the local loopback',result.stderr)
