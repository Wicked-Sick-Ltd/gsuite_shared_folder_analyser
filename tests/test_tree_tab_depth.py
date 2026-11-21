import os
import runpy
import sys
import tempfile
import types
import unittest
import unittest.mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = 'application/vnd.google-apps.folder'
DRIVE_ID = 'drive-1'
PERSIAN = '\u067e\u0631\u0648\u0698\u0647\u200c\u0647\u0627'

EXPECTED = [
    '\\tFinance/',
    'A\u200dB/',
    'Finance/',
    '  Payroll/',
    'Go\\u202eBack/',
    'Line\\nBreak/',
    '\\\\tFinance/',
    PERSIAN + '/',
    '\\u200dHidden/',
]


def folder(file_id, name, parent):
    return {
        'id': file_id,
        'name': name,
        'mimeType': FOLDER,
        'parents': [parent],
        'modifiedTime': '2026-01-01T00:00:00Z',
    }


FILES = [
    folder('finance', 'Finance', DRIVE_ID),
    folder('payroll', 'Payroll', 'finance'),
    folder('tab', '\tFinance', DRIVE_ID),
    folder('literal', '\\tFinance', DRIVE_ID),
    folder('zwj', 'A\u200dB', DRIVE_ID),
    folder('bidi', 'Go\u202eBack', DRIVE_ID),
    folder('newline', 'Line\nBreak', DRIVE_ID),
    folder('persian', PERSIAN, DRIVE_ID),
    folder('leading-joiner', '\u200dHidden', DRIVE_ID),
]


class _Request:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _Service:
    def files(self):
        class Files:
            def list(self, **kwargs):
                return _Request({'files': FILES})
        return Files()

    def drives(self):
        class Drives:
            def list(self, **kwargs):
                return _Request({'drives': [{'id': DRIVE_ID, 'name': 'Team'}]})
        return Drives()


def stub_modules():
    google = types.ModuleType('google')
    oauth2 = types.ModuleType('google.oauth2')
    service_account = types.ModuleType('google.oauth2.service_account')
    service_account.Credentials = types.SimpleNamespace(
        from_service_account_file=lambda *args, **kwargs: object()
    )
    oauth2.service_account = service_account
    google.oauth2 = oauth2

    googleapiclient = types.ModuleType('googleapiclient')
    discovery = types.ModuleType('googleapiclient.discovery')
    discovery.build = lambda *args, **kwargs: _Service()
    googleapiclient.discovery = discovery

    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *args, **kwargs: None

    tqdm_mod = types.ModuleType('tqdm')
    tqdm_mod.tqdm = lambda iterable, **kwargs: iterable

    return {
        'google': google,
        'google.oauth2': oauth2,
        'google.oauth2.service_account': service_account,
        'googleapiclient': googleapiclient,
        'googleapiclient.discovery': discovery,
        'dotenv': dotenv,
        'tqdm': tqdm_mod,
    }


class TreeDepthTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.key = self.tmp / 'placeholder-service-account.json'
        self.key.write_text('{"placeholder": true}')

    def _run(self, script, argv, extra_env):
        env = {'SERVICE_ACCOUNT_FILE': str(self.key)}
        env.update(extra_env)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with unittest.mock.patch.dict(sys.modules, stub_modules()), \
                    unittest.mock.patch.dict(os.environ, env), \
                    unittest.mock.patch.object(sys, 'argv', [script] + argv), \
                    redirect_stdout(StringIO()) as out:
                runpy.run_path(str(REPO_ROOT / script), run_name='__main__')
        finally:
            os.chdir(cwd)
        self.assertNotIn('Error processing', out.getvalue())

    def test_single_drive_tree_keeps_depth_and_escapes(self):
        tree = self.tmp / 'tree.txt'
        self._run(
            'shared_drive_analysis.py',
            ['--drive-id', DRIVE_ID],
            {'OUTPUT_CSV': str(self.tmp / 'report.csv'), 'OUTPUT_TREE': str(tree)},
        )
        self.assertEqual(tree.read_text(encoding='utf-8').splitlines(), EXPECTED)

    def test_orgwide_tree_matches_the_single_drive_tree(self):
        self._run('shared_drive_orgwide_analysis.py', [], {})
        trees = list((self.tmp / 'reports').glob('*_tree_Team.txt'))
        self.assertEqual(len(trees), 1)
        self.assertEqual(trees[0].read_text(encoding='utf-8').splitlines(), EXPECTED)


if __name__ == '__main__':
    unittest.main()
