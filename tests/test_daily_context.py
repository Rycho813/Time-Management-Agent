import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('daily_context',ROOT/'deploy/prepare_daily_context.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class DailyContext(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.repo=self.root/'source';self.repo.mkdir()
        for name in module.REQUIRED:
            destination=self.repo/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,destination)
        (self.repo/'.dockerignore').write_text('*\n!time_summary.py\n')
        (self.repo/'.env').write_text('SYNTHETIC_SECRET=do-not-export\n')
        self.git('init','-q');self.git('add','.')
        self.git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture')
        self.sha=self.git('rev-parse','HEAD').strip()
    def tearDown(self):self.tmp.cleanup()
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.repo),*args],text=True)
    def test_pinned_daily_and_new_matcher_export_without_env_or_ignore_rules(self):
        before=self.git('status','--porcelain');target=self.root/'context'
        result=module.prepare(self.repo,self.sha,target)
        self.assertTrue((target/'migrate_and_summary.py').is_file())
        self.assertTrue((target/'backend/place_matching.py').is_file())
        self.assertIn('COPY --chown=10001:10001 backend/',(target/'Dockerfile').read_text())
        self.assertFalse((target/'.env').exists());self.assertFalse((target/'.dockerignore').exists())
        self.assertFalse((target/'.git').exists());self.assertEqual(before,self.git('status','--porcelain'))
        self.assertEqual(result['agent_commit'],self.sha)
        with self.assertRaises(ValueError):module.prepare(self.repo,self.sha,target)
    def test_wrong_commit_dirty_checkout_and_selected_symlink_refused(self):
        with self.assertRaises(ValueError):module.prepare(self.repo,'a'*40,self.root/'wrong')
        (self.repo/'notion_read.py').write_text('changed')
        with self.assertRaises(ValueError):module.prepare(self.repo,self.sha,self.root/'dirty')
        self.git('checkout','--','notion_read.py')
        (self.repo/'notion_read.py').unlink();(self.repo/'notion_read.py').symlink_to('/etc/passwd')
        self.git('add','notion_read.py');self.git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','symlink')
        sha=self.git('rev-parse','HEAD').strip()
        with self.assertRaises(ValueError):module.prepare(self.repo,sha,self.root/'link')
