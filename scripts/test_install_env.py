"""Install the task requirements unchanged, with its selected framework wheels."""
from pathlib import Path
import sys
import json
import subprocess
import zipfile
import tempfile
import unittest
from unittest.mock import patch
import install_env as installer


class RequirementsTests(unittest.TestCase):
    def setUp(self):
        parent=installer.DATA/'run_data';parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='.requirements-test-',dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.wheels={'torch':self.root/'torch.whl','torch-npu':self.root/'torch_npu.whl'}

    def plan(self, content):
        path=self.root/'requirements.txt';path.write_text(content,encoding='utf-8')
        result=installer.prepare_requirements(self.root,self.wheels)
        self.assertEqual(path.read_text(encoding='utf-8'),content)
        return result

    def test_default_file_changes_do_not_add_dependencies_to_existing_task(self):
        defaults=self.root/'requirements-default.txt'
        defaults.write_text('pytest==9.0.0\nunrequested-package==1.0\n')
        with patch.object(installer,'ROOT',self.root):
            first=self.plan('pytest==8.3.0\n')
            defaults.unlink()
            second=installer.prepare_requirements(self.root,self.wheels)
        self.assertEqual(first,second)
        self.assertNotIn('unrequested-package',first)
        self.assertNotIn('pytest==9.0.0',first)
        self.assertIn('-r '+str(self.root/'requirements.txt'),first)

    def test_unpinned_requirement_is_not_replaced_with_a_default_version(self):
        result=self.plan('typing_extensions\n')
        self.assertEqual((self.root/'requirements.txt').read_text(),'typing_extensions\n')
        self.assertEqual(sum(line.startswith('-r ') for line in result.splitlines()),1)

    def test_environment_markers_remain_in_the_original_task_file(self):
        original='numpy==2.0.0; python_version >= "3.12"\n'
        self.plan(original)
        self.assertEqual((self.root/'requirements.txt').read_text(),original)

    def test_nested_requirements_constraints_and_pip_options_are_preserved(self):
        (self.root/'nested.txt').write_text('pytest==8.3.0\n')
        (self.root/'constraints.txt').write_text('numpy>=2.0\n')
        (self.root/'wheelhouse').mkdir()
        original='--find-links ./wheelhouse\n-r nested.txt\n-c constraints.txt\n'
        self.plan(original)
        self.assertEqual((self.root/'requirements.txt').read_text(),original)
        self.assertEqual((self.root/'constraints.txt').read_text(),'numpy>=2.0\n')

    def test_task_framework_constraints_and_selected_wheels_are_both_supplied(self):
        result=self.plan('torch==1.0.0\ntorch-npu==1.0.0\n')
        self.assertIn('torch @ '+self.wheels['torch'].resolve().as_uri(),result)
        self.assertIn('torch-npu @ '+self.wheels['torch-npu'].resolve().as_uri(),result)
        self.assertEqual((self.root/'requirements.txt').read_text(),'torch==1.0.0\ntorch-npu==1.0.0\n')

    def test_basic_information_and_requirements_support_ci_pr_and_api_sources(self):
        task_dir=self.root/'run';task_dir.mkdir()
        wheels={}
        for name in ('torch','torch_npu'):
            wheel=self.root/(name+'-1.0.0-py3-none-any.whl')
            with zipfile.ZipFile(wheel,'w') as z:
                z.writestr(name+'-1.0.0.dist-info/METADATA',
                    'Metadata-Version: 2.1\nName: '+name+'\nVersion: 1.0.0\n')
            wheels[name]=str(wheel)
        (task_dir/'requirements.txt').write_text('packaging==25.0\n')
        base=dict(python_version='.'.join(map(str,sys.version_info[:3])),pytorch_version='1.0.0',
                  pytorch_source=str(self.root),pytorch_wheel=wheels['torch'],torch_npu_version='1.0.0',
                  torch_npu_wheel=wheels['torch_npu'])
        for context in ({'source':{'type':'ci','run_id':'example'}},{'source':{'type':'pr','number':123}},
                        {'source':{'type':'api'},'problem-description':'API `torch.add` needs adaptation'}):
            (task_dir/'basic_information.json').write_text(json.dumps(base|context))
            result=subprocess.run([sys.executable,'-B',str(installer.ROOT/'scripts/install_env.py'),
                                   '--run-dir',str(task_dir),'--dry-run'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('-r '+str(task_dir/'requirements.txt'),result.stdout)
            self.assertEqual({p.name for p in task_dir.iterdir()},{'basic_information.json','requirements.txt'})

    def test_independent_requirements_file_is_required(self):
        with self.assertRaisesRegex(ValueError,'requirements.txt missing'):
            installer.prepare_requirements(self.root,self.wheels)

    def test_exact_interpreter_version_required(self):
        with self.assertRaises(ValueError):installer.find_python('3.11',explicit=sys.executable)
        with self.assertRaises(ValueError):installer.find_python('0.0.0',explicit=sys.executable)


if __name__=='__main__':unittest.main(verbosity=2)
