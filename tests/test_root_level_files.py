import csv
import os
import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = 'application/vnd.google-apps.folder'
DRIVE_ID = 'drive-under-test'
DRIVE_NAME = 'Team Drive'

LISTING = [
    {
        'id': 'folder-a', 'name': 'Projects', 'mimeType': FOLDER,
        'parents': [DRIVE_ID], 'modifiedTime': '2026-01-01T00:00:00Z',
        'owners': [{'emailAddress': 'owner@example.test'}],
    },
    {
        'id': 'file-in-folder', 'name': 'plan.pdf', 'mimeType': 'application/pdf',
        'parents': ['folder-a'], 'size': '100', 'modifiedTime': '2026-01-02T00:00:00Z',
        'owners': [{'emailAddress': 'owner@example.test'}],
    },
    {
        'id': 'root-file-id', 'name': 'root-secret.bin', 'mimeType': 'application/octet-stream',
        'parents': [DRIVE_ID], 'size': '4096', 'modifiedTime': '2026-03-03T00:00:00Z',
        'owners': [{'emailAddress': 'contributor@example.test'}],
    },
]


class _Request:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeFiles:
    def __init__(self, pages):
        self._pages = pages

    def list(self, **kwargs):
        assert kwargs.get('driveId') == DRIVE_ID
        token = kwargs.get('pageToken')
        index = 0 if token is None else int(token)
        response = {'files': self._pages[index]}
        if index + 1 < len(self._pages):
            response['nextPageToken'] = str(index + 1)
        return _Request(response)


class _FakeDrives:
    def list(self, **kwargs):
        return _Request({'drives': [{'id': DRIVE_ID, 'name': DRIVE_NAME}]})


class _FakeService:
    def __init__(self, pages):
        self._pages = pages

    def files(self):
        return _FakeFiles(self._pages)

    def drives(self):
        return _FakeDrives()


def _stub_modules(pages):
    google = types.ModuleType('google')
    oauth2 = types.ModuleType('google.oauth2')
    service_account = types.ModuleType('google.oauth2.service_account')
    service_account.Credentials = types.SimpleNamespace(
        from_service_account_file=lambda *a, **k: object()
    )
    oauth2.service_account = service_account
    google.oauth2 = oauth2

    googleapiclient = types.ModuleType('googleapiclient')
    discovery = types.ModuleType('googleapiclient.discovery')
    discovery.build = lambda *a, **k: _FakeService(pages)
    googleapiclient.discovery = discovery

    tqdm_mod = types.ModuleType('tqdm')
    tqdm_mod.tqdm = lambda iterable, **k: iterable

    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *a, **k: None

    return {
        'google': google,
        'google.oauth2': oauth2,
        'google.oauth2.service_account': service_account,
        'googleapiclient': googleapiclient,
        'googleapiclient.discovery': discovery,
        'tqdm': tqdm_mod,
        'dotenv': dotenv,
    }


class _ScriptHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.key_file = self.tmp / 'placeholder-service-account.json'
        self.key_file.write_text('{"placeholder": true}')

    def _run(self, script, argv, pages, extra_env=None):
        env = {'SERVICE_ACCOUNT_FILE': str(self.key_file)}
        env.update(extra_env or {})
        old_cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with mock.patch.dict(sys.modules, _stub_modules(pages)), \
                    mock.patch.dict(os.environ, env), \
                    mock.patch.object(sys, 'argv', [script] + argv), \
                    mock.patch('builtins.print'):
                runpy.run_path(str(REPO_ROOT / script), run_name='__main__')
        finally:
            os.chdir(old_cwd)

    def _read_csv(self, path):
        with open(path, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def assertRootFileReported(self, rows, tree_text, file_id, name, size, owner):
        matching = [r for r in rows if file_id in r.values()]
        self.assertEqual(len(matching), 1, f'no CSV row for root-level file {name!r}: {rows}')
        row = matching[0]
        self.assertEqual(row['Folder Path'], name)
        self.assertEqual(row['File Count'], '1')
        self.assertEqual(row['Total Size (Bytes)'], str(size))
        self.assertEqual(row['Owner Email'], owner)
        self.assertIn(name, tree_text.splitlines())

    def assertDriveTotal(self, rows, count, size):
        totals = [r for r in rows if DRIVE_ID in r.values()]
        self.assertEqual(len(totals), 1, f'no drive-root total row: {rows}')
        self.assertEqual(totals[0]['File Count'], str(count))
        self.assertEqual(totals[0]['Total Size (Bytes)'], str(size))


class SingleDriveRootFilesTest(_ScriptHarness):
    def _run_single(self, pages):
        self._run(
            'shared_drive_analysis.py', ['--drive-id', DRIVE_ID], pages,
            {'OUTPUT_CSV': str(self.tmp / 'report.csv'), 'OUTPUT_TREE': str(self.tmp / 'tree.txt')},
        )
        return self._read_csv(self.tmp / 'report.csv'), (self.tmp / 'tree.txt').read_text()

    def test_root_level_file_is_reported(self):
        rows, tree = self._run_single([LISTING])
        self.assertRootFileReported(
            rows, tree, 'root-file-id', 'root-secret.bin', 4096, 'contributor@example.test'
        )
        self.assertDriveTotal(rows, count=2, size=4196)

    def test_root_level_file_on_later_page_is_reported(self):
        rows, tree = self._run_single([LISTING[:2], LISTING[2:]])
        self.assertRootFileReported(
            rows, tree, 'root-file-id', 'root-secret.bin', 4096, 'contributor@example.test'
        )

    def test_files_without_a_listed_parent_are_reported(self):
        listing = LISTING + [
            {'id': 'no-parents', 'name': 'loose.txt', 'mimeType': 'text/plain', 'size': '7',
             'owners': [{'emailAddress': 'a@example.test'}]},
            {'id': 'unknown-parent', 'name': 'stray.txt', 'mimeType': 'text/plain',
             'parents': ['not-in-listing'], 'size': '9',
             'owners': [{'emailAddress': 'b@example.test'}]},
        ]
        rows, tree = self._run_single([listing])
        self.assertRootFileReported(rows, tree, 'no-parents', 'loose.txt', 7, 'a@example.test')
        self.assertRootFileReported(rows, tree, 'unknown-parent', 'stray.txt', 9, 'b@example.test')
        self.assertDriveTotal(rows, count=4, size=4212)

    def test_folder_rows_unchanged(self):
        rows, tree = self._run_single([LISTING])
        folder_rows = [r for r in rows if r['MimeType'] == 'folder']
        self.assertEqual(len(folder_rows), 1)
        self.assertEqual(folder_rows[0]['Folder Path'], 'Projects')
        self.assertEqual(folder_rows[0]['File Count'], '1')
        self.assertEqual(folder_rows[0]['Total Size (Bytes)'], '100')
        self.assertEqual(tree.splitlines()[0], 'Projects/')
        self.assertNotIn('plan.pdf', tree)


class OrgWideRootFilesTest(_ScriptHarness):
    def test_root_level_file_is_reported(self):
        self._run('shared_drive_orgwide_analysis.py', [], [LISTING])
        reports = self.tmp / 'reports'
        csv_files = list(reports.glob('*_report_Team_Drive.csv'))
        tree_files = list(reports.glob('*_tree_Team_Drive.txt'))
        self.assertEqual(len(csv_files), 1)
        self.assertEqual(len(tree_files), 1)
        rows = self._read_csv(csv_files[0])
        tree = tree_files[0].read_text()
        self.assertRootFileReported(
            rows, tree, 'root-file-id', 'root-secret.bin', 4096, 'contributor@example.test'
        )
        self.assertDriveTotal(rows, count=2, size=4196)


if __name__ == '__main__':
    unittest.main()
