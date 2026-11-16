import csv
import glob
import os
import runpy
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER_MIME = 'application/vnd.google-apps.folder'
DRIVE_ID = 'drive-1'

DRIVE_FILES = [
    {'id': 'folder-1', 'name': 'Docs', 'mimeType': FOLDER_MIME,
     'parents': [DRIVE_ID], 'modifiedTime': '2026-01-01T00:00:00Z'},
    {'id': 'file-nested', 'name': 'nested.txt', 'mimeType': 'text/plain',
     'parents': ['folder-1'], 'size': '100', 'modifiedTime': '2026-01-02T00:00:00Z'},
    {'id': 'file-root', 'name': 'root-level.bin', 'mimeType': 'application/octet-stream',
     'parents': [DRIVE_ID], 'size': '5000', 'modifiedTime': '2026-03-01T00:00:00Z',
     'owners': [{'emailAddress': 'contributor@example.com'}]},
]


class _Request:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeDriveService:
    def drives(self):
        service = types.SimpleNamespace()
        service.list = lambda **kwargs: _Request({'drives': [{'id': DRIVE_ID, 'name': 'Team Drive'}]})
        return service

    def files(self):
        def list_files(**kwargs):
            assert kwargs['driveId'] == DRIVE_ID
            return _Request({'files': [dict(f) for f in DRIVE_FILES]})
        service = types.SimpleNamespace()
        service.list = list_files
        return service


def _stub_modules():
    google = types.ModuleType('google')
    oauth2 = types.ModuleType('google.oauth2')
    service_account = types.ModuleType('google.oauth2.service_account')
    service_account.Credentials = types.SimpleNamespace(
        from_service_account_file=lambda *args, **kwargs: object()
    )
    google.oauth2 = oauth2
    oauth2.service_account = service_account

    googleapiclient = types.ModuleType('googleapiclient')
    discovery = types.ModuleType('googleapiclient.discovery')
    discovery.build = lambda *args, **kwargs: _FakeDriveService()
    googleapiclient.discovery = discovery

    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *args, **kwargs: None

    tqdm_module = types.ModuleType('tqdm')
    tqdm_module.tqdm = lambda iterable, **kwargs: iterable

    return {
        'google': google,
        'google.oauth2': oauth2,
        'google.oauth2.service_account': service_account,
        'googleapiclient': googleapiclient,
        'googleapiclient.discovery': discovery,
        'dotenv': dotenv,
        'tqdm': tqdm_module,
    }


class RootLevelFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.service_account_file = os.path.join(self.workdir, 'service_account_placeholder.json')
        with open(self.service_account_file, 'w') as f:
            f.write('{}')
        self._cwd = os.getcwd()
        os.chdir(self.workdir)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run_script(self, name, argv, env):
        env = dict(env, SERVICE_ACCOUNT_FILE=self.service_account_file)
        with mock.patch.dict(sys.modules, _stub_modules()), \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, 'argv', argv), \
                redirect_stdout(StringIO()) as out:
            runpy.run_path(os.path.join(REPO_ROOT, name), run_name='__main__')
        return out.getvalue()

    def _read_rows(self, csv_path):
        with open(csv_path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def _assert_root_files_reported(self, rows):
        root_file_rows = [r for r in rows if r['Folder Path'] == 'root-level.bin']
        self.assertEqual(len(root_file_rows), 1, rows)
        self.assertEqual(root_file_rows[0]['Total Size (Bytes)'], '5000')
        self.assertEqual(root_file_rows[0]['File Count'], '1')
        self.assertEqual(root_file_rows[0]['Owner Email'], 'contributor@example.com')
        self.assertEqual(root_file_rows[0]['MimeType'], 'application/octet-stream')

        drive_rows = [r for r in rows if r['MimeType'] == 'drive']
        self.assertEqual(len(drive_rows), 1, rows)
        self.assertEqual(drive_rows[0]['Total Size (Bytes)'], '5100')
        self.assertEqual(drive_rows[0]['File Count'], '2')
        self.assertEqual(drive_rows[0]['Latest Modified Time'], '2026-03-01T00:00:00Z')

        folder_rows = [r for r in rows if r['Folder Path'] == 'Docs']
        self.assertEqual(len(folder_rows), 1, rows)
        self.assertEqual(folder_rows[0]['Total Size (Bytes)'], '100')
        self.assertEqual(folder_rows[0]['File Count'], '1')

    def test_orgwide_report_includes_files_at_drive_root(self):
        output = self._run_script('shared_drive_orgwide_analysis.py', ['shared_drive_orgwide_analysis.py'], {})
        self.assertNotIn('Error processing', output)
        reports = glob.glob(os.path.join(self.workdir, 'reports', '*_report_Team_Drive.csv'))
        self.assertEqual(len(reports), 1)
        self._assert_root_files_reported(self._read_rows(reports[0]))

    def test_single_drive_report_includes_files_at_drive_root(self):
        csv_path = os.path.join(self.workdir, 'report.csv')
        self._run_script(
            'shared_drive_analysis.py',
            ['shared_drive_analysis.py', '--drive-id', DRIVE_ID],
            {'OUTPUT_CSV': csv_path, 'OUTPUT_TREE': os.path.join(self.workdir, 'tree.txt')},
        )
        self._assert_root_files_reported(self._read_rows(csv_path))


if __name__ == '__main__':
    unittest.main()
