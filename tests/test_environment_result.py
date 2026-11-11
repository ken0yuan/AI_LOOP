"""Environment readiness lives in basic_information.json, including failure and retry."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import install_env as installer


class EnvironmentResultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data'
        self.run=self.data/'run_data/adapt-torch.add-261109';(self.run/'log').mkdir(parents=True)
        source=self.data/'source_code/pytorch';source.mkdir(parents=True)
        wheels=self.data/'wheels';wheels.mkdir()
        self.version='3.11.17';self.probe_python=self.version;self.probe_pytorch='1.0.0';self.fail_install=False
        self.installation_statuses=[]
        task=dict(preparation_status='INPUTS_READY',environment_status='NOT_READY',python_version=self.version,
                  pytorch_source='data/source_code/pytorch',pytorch_version='1.0.0',torch_npu_version='1.0.0',
                  caller_note='preserve this',**{'problem-description':'API `torch.add` needs adaptation'})
        for name,key in [('torch','pytorch_wheel'),('torch_npu','torch_npu_wheel')]:
            wheel=wheels/(name+'-1.0.0-py3-none-any.whl')
            with zipfile.ZipFile(wheel,'w') as archive:
                archive.writestr(name+'-1.0.0.dist-info/METADATA',
                    'Metadata-Version: 2.1\nName: '+name+'\nVersion: 1.0.0\n')
            task[key]='data/wheels/'+wheel.name
        self.basic=self.run/'basic_information.json';self.basic.write_text(json.dumps(task))
        (self.run/'requirements.txt').write_text('packaging==25.0\n')
        (self.run/'log/step0-preparation.json').write_text(json.dumps({'status':'INPUTS_READY'}))
        (self.run/'log/step0-source.json').write_text(json.dumps({'status':'SOURCE_READY'}))
        self.venv=self.data/'venv'/('python-'+self.version)

    def run_command(self, argv, **kwargs):
        args=list(map(str,argv))
        if args[-1]=='import platform;print(platform.python_version())':
            return self.version
        if len(args)>2 and args[1:3]==['-m','venv']:
            (self.venv/'bin').mkdir(parents=True)
            (self.venv/'bin/python').write_text('fixture')
            (self.venv/'bin/activate').write_text('')
        if len(args)>2 and args[1:3]==['-m','pip']:
            self.installation_statuses.append(json.loads(self.basic.read_text())['environment_status'])
            if self.fail_install:raise RuntimeError('fixture install failed')
        if 'pytorch_file=' in args[-1]:
            return json.dumps(dict(python_version=self.probe_python,pytorch_version=self.probe_pytorch,torch_npu_version='1.0.0',
                pytorch_file=str(self.venv/'lib/torch/__init__.py'),torch_npu_file=str(self.venv/'lib/torch_npu/__init__.py')))
        return ''

    def invoke(self, dry_run=False):
        args=['install_env.py','--run-dir',str(self.run)]+(['--dry-run'] if dry_run else [])
        self.errors=io.StringIO()
        with patch.object(installer,'ROOT',self.root),patch.object(installer,'DATA',self.data), \
                patch.object(installer,'find_python',return_value=sys.executable), \
                patch.object(installer,'run',side_effect=self.run_command), \
                patch.object(installer.subprocess,'run',return_value=SimpleNamespace(stdout=b'')), \
                patch.object(sys,'argv',args),redirect_stdout(io.StringIO()),redirect_stderr(self.errors):
            return installer.main()

    def test_success_updates_basic_information_and_keeps_business_inputs(self):
        legacy=json.loads(self.basic.read_text())
        legacy.update(installed_pytorch_version='old',installed_torch_npu_version='old')
        self.basic.write_text(json.dumps(legacy))
        self.assertEqual(self.invoke(),0,self.errors.getvalue())
        result=json.loads(self.basic.read_text())
        self.assertEqual(result['environment_status'],'ENV_READY')
        self.assertEqual(result['python'],'data/venv/python-3.11.17/bin/python')
        self.assertEqual(result['activate'],'data/venv/python-3.11.17/env.sh')
        self.assertNotIn('installed_pytorch_version',result)
        self.assertNotIn('installed_torch_npu_version',result)
        self.assertEqual(result['caller_note'],'preserve this')
        self.assertEqual(result['problem-description'],'API `torch.add` needs adaptation')
        self.assertEqual(result['preparation_status'],'INPUTS_READY')
        self.assertTrue(all(not Path(result[key]).is_absolute() and (self.root/result[key]).exists()
                            for key in ('python','venv','activate')))
        self.assertTrue(all(status=='INSTALLING' for status in self.installation_statuses))
        self.assertFalse(list((self.run/'log').glob('*environment.json')))

    def test_failure_removes_old_ready_state_and_retry_can_restore_it(self):
        self.assertEqual(self.invoke(),0,self.errors.getvalue())
        self.fail_install=True
        self.assertEqual(self.invoke(),1)
        failed=json.loads(self.basic.read_text())
        self.assertEqual(failed['environment_status'],'FAILED')
        self.assertIn('fixture install failed',failed['environment_error'])
        self.assertNotIn('python',failed)
        self.assertNotIn('environment_completed_at',failed)
        self.assertEqual(failed['caller_note'],'preserve this')
        self.fail_install=False
        self.assertEqual(self.invoke(),0,self.errors.getvalue())
        ready=json.loads(self.basic.read_text())
        self.assertEqual(ready['environment_status'],'ENV_READY')
        self.assertTrue(ready['environment_reused'])
        self.assertNotIn('environment_error',ready)

    def test_dry_run_does_not_modify_basic_information_or_create_environment(self):
        before=self.basic.read_bytes()
        self.assertEqual(self.invoke(dry_run=True),0,self.errors.getvalue())
        self.assertEqual(self.basic.read_bytes(),before)
        self.assertFalse(self.venv.exists())
        self.assertFalse((self.run/'log/.step0-entry.lock').exists())

    def test_framework_probe_mismatch_still_prevents_environment_ready(self):
        self.probe_pytorch='9.9.0'
        self.assertEqual(self.invoke(),1)
        result=json.loads(self.basic.read_text())
        self.assertEqual(result['environment_status'],'FAILED')
        self.assertIn('pytorch_version',result['environment_error'])
        self.assertNotIn('installed_pytorch_version',result)

    def test_python_probe_mismatch_cannot_mark_environment_ready(self):
        self.probe_python='3.11.18'
        self.assertEqual(self.invoke(),1)
        result=json.loads(self.basic.read_text())
        self.assertEqual(result['environment_status'],'FAILED')
        self.assertIn('Python version',result['environment_error'])
        self.assertNotIn('python',result)


if __name__=='__main__':
    unittest.main(verbosity=2)
