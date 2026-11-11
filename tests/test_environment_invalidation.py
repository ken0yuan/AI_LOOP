"""Refetching task materials invalidates an environment that matched previous inputs."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import fetch_nightly_wheels as fetch
import test_prepare_api_inputs as fixtures


class EnvironmentInvalidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.PreparationTests.setUpClass()

    def setUp(self):
        self.f=fixtures.PreparationTests('runTest');self.f.setUp();self.addCleanup(self.f.tearDown)
        self.downloader,*_=self.f.fixtures();self.path=self.f.seed()

    def previous_ready(self):
        basic=json.loads(self.path.read_text())
        basic.update(environment_status='ENV_READY',python='data/venv/old/bin/python',
                     venv='data/venv/old',activate='data/venv/old/env.sh',caller_note='keep')
        self.path.write_text(json.dumps(basic))

    def invoke(self):
        with patch.object(fetch,'load_provider',return_value=(self.downloader,self.f.identity,self.f.selector)), \
                redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            return fetch.main(['--basic-information',str(self.path),'--data-root',str(self.f.data)])

    def test_refetch_resets_environment_readiness_and_preserves_task_context(self):
        self.assertEqual(self.invoke(),0)
        self.previous_ready()
        original=fetch.source_fetch.complete_sources
        def check_before_source_mutation(*args,**kwargs):
            current=json.loads(self.path.read_text())
            self.assertEqual(current['environment_status'],'NOT_READY')
            self.assertNotIn('python',current)
            return original(*args,**kwargs)
        with patch.object(fetch.source_fetch,'complete_sources',side_effect=check_before_source_mutation):
            self.assertEqual(self.invoke(),0)
        result=json.loads(self.path.read_text())
        self.assertEqual(result['environment_status'],'NOT_READY')
        self.assertEqual(result['preparation_status'],'INPUTS_READY')
        self.assertEqual(result['caller_note'],'keep')
        self.assertNotIn('python',result)
        self.assertNotIn('activate',result)

    def test_failed_refetch_cannot_leave_previous_environment_ready(self):
        self.previous_ready()
        def fail_selection(config):
            self.assertEqual(json.loads(self.path.read_text())['environment_status'],'NOT_READY')
            raise RuntimeError('selection failed')
        with patch.object(fetch,'resolve',side_effect=fail_selection),redirect_stderr(io.StringIO()):
            self.assertEqual(fetch.main(['--basic-information',str(self.path),'--data-root',str(self.f.data)]),1)
        result=json.loads(self.path.read_text())
        self.assertEqual(result['environment_status'],'NOT_READY')
        self.assertEqual(result['preparation_status'],'FAILED')
        self.assertEqual(result['caller_note'],'keep')
        self.assertNotIn('python',result)

    def test_dry_run_keeps_previous_environment_state_and_paths(self):
        self.previous_ready();before=self.path.read_bytes()
        with patch.object(fetch,'load_provider',return_value=(self.downloader,self.f.identity,self.f.selector)), \
                redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(fetch.main(['--basic-information',str(self.path),'--data-root',str(self.f.data),'--dry-run']),0)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse((self.f.data/'source_code').exists())


if __name__=='__main__':unittest.main(verbosity=2)
