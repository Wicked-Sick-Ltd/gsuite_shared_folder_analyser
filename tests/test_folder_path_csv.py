import csv
import itertools
import os
import runpy
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = 'application/vnd.google-apps.folder'
DRIVE_ID = 'drive-1'


class FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class FakeDriveService:
    def __init__(self, files):
        self._files = files

    def drives(self):
        class Drives:
            def list(self, **kwargs):
                return FakeRequest({'drives': [{'id': DRIVE_ID, 'name': 'Team Drive'}]})

        return Drives()

    def files(self):
        service = self

        class Files:
            def list(self, **kwargs):
                return FakeRequest({'files': service._files})

        return Files()


def folder(file_id, name, parent):
    return {
        'id': file_id,
        'name': name,
        'mimeType': FOLDER,
        'parents': [parent],
        'modifiedTime': '2026-01-01T00:00:00Z',
        'owners': [{'emailAddress': 'owner@example.com'}],
    }


# A genuine Finance folder containing Payroll, plus a top-level folder whose
# name is crafted to render as the same nested path.
SPOOF_FILES = [
    folder('finance', 'Finance', DRIVE_ID),
    folder('payroll', 'Payroll', 'finance'),
    folder('spoof', 'Finance/Payroll', DRIVE_ID),
    folder('trailing', 'Finance\\', DRIVE_ID),
    folder('trailing-child', 'Payroll', 'trailing'),
]


def stub_modules(files):
    google = types.ModuleType('google')
    oauth2 = types.ModuleType('google.oauth2')
    service_account = types.ModuleType('google.oauth2.service_account')
    service_account.Credentials = unittest.mock.Mock()
    oauth2.service_account = service_account
    google.oauth2 = oauth2

    googleapiclient = types.ModuleType('googleapiclient')
    discovery = types.ModuleType('googleapiclient.discovery')
    discovery.build = lambda *args, **kwargs: FakeDriveService(files)
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


class FolderPathCsvMixin:
    def run_script(self, tmp):
        """Run the script and return (globals, csv_path, tree_path)."""
        raise NotImplementedError

    def run_with(self, files, reader):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp, 'placeholder-service-account')
            credentials.write_text('placeholder')
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with unittest.mock.patch.dict(sys.modules, stub_modules(files)), \
                        unittest.mock.patch.dict(os.environ, {
                            'SERVICE_ACCOUNT_FILE': str(credentials),
                            'OUTPUT_CSV': str(Path(tmp, 'report.csv')),
                            'OUTPUT_TREE': str(Path(tmp, 'tree.txt')),
                        }):
                    return reader(*self.run_script(Path(tmp)))
            finally:
                os.chdir(cwd)

    def read_rows(self, files=SPOOF_FILES):
        def reader(_, csv_path, __):
            with open(csv_path, newline='', encoding='utf-8') as f:
                return list(csv.DictReader(f))
        return self.run_with(files, reader)

    def test_tree_escapes_slash_in_name(self):
        lines = self.run_with(
            SPOOF_FILES, lambda _, __, tree_path: tree_path.read_text(encoding='utf-8').splitlines()
        )
        self.assertIn('Finance\\/Payroll/', lines)
        self.assertNotIn('Finance/Payroll/', lines)

    def test_distinct_name_chains_never_share_a_path(self):
        get_folder_path = self.run_with(SPOOF_FILES, lambda g, _, __: g['get_folder_path'])
        alphabet = ['a', '/', '\\']
        names = [''.join(p) for n in (1, 2, 3) for p in itertools.product(alphabet, repeat=n)]
        seen = {}
        for depth in (1, 2):
            for chain in itertools.product(names, repeat=depth):
                file_map = {}
                parent = DRIVE_ID
                for i, name in enumerate(chain):
                    file_map[f'f{i}'] = folder(f'f{i}', name, parent)
                    parent = f'f{i}'
                path = get_folder_path(parent, file_map)
                self.assertFalse(
                    path in seen, f'{chain!r} collides with {seen.get(path)!r} as {path!r}'
                )
                seen[path] = chain

    def test_slash_in_name_does_not_spoof_nested_path(self):
        rows = self.read_rows()
        paths = [row['Folder Path'] for row in rows]
        self.assertEqual(len(paths), len(set(paths)), paths)

        by_id = {row['Folder ID']: row for row in rows}
        self.assertEqual(by_id['payroll']['Folder Path'], 'Finance/Payroll')
        self.assertEqual(by_id['spoof']['Folder Path'], 'Finance\\/Payroll')
        self.assertEqual(by_id['trailing-child']['Folder Path'], 'Finance\\\\/Payroll')

    def test_id_columns_follow_the_original_columns(self):
        def reader(_, csv_path, __):
            with open(csv_path, newline='', encoding='utf-8') as f:
                return next(csv.reader(f))
        header = self.run_with(SPOOF_FILES, reader)
        self.assertEqual(header, [
            'Folder Path', 'File Count', 'Total Size (Bytes)', 'Readable Size',
            'Latest Modified Time', 'Owner Email', 'MimeType',
            'Folder ID', 'Parent ID',
        ])

    def test_rows_carry_folder_and_parent_ids(self):
        rows = self.read_rows()
        folder_rows = [row for row in rows if row['MimeType'] == 'folder']
        by_id = {row['Folder ID']: row for row in folder_rows}
        self.assertEqual(set(by_id), {f['id'] for f in SPOOF_FILES})
        self.assertEqual(by_id['payroll']['Parent ID'], 'finance')
        self.assertEqual(by_id['spoof']['Parent ID'], DRIVE_ID)
        drive_rows = [row for row in rows if row['MimeType'] == 'drive']
        self.assertEqual(len(drive_rows), 1)
        self.assertEqual(drive_rows[0]['Folder Path'], '/')
        self.assertEqual(drive_rows[0]['Folder ID'], DRIVE_ID)
        self.assertEqual(drive_rows[0]['Parent ID'], '')

    def test_lookalike_slash_rows_are_distinguished_by_parent_id(self):
        files = [
            folder('finance', 'Finance', DRIVE_ID),
            folder('payroll', 'Payroll', 'finance'),
            folder('lookalike', 'Finance\u2215Payroll', DRIVE_ID),
        ]
        by_id = {row['Folder ID']: row for row in self.read_rows(files)}
        self.assertNotEqual(by_id['payroll']['Parent ID'], by_id['lookalike']['Parent ID'])


class OrgWideScriptTest(FolderPathCsvMixin, unittest.TestCase):
    def run_script(self, tmp):
        g = runpy.run_path(str(REPO_ROOT / 'shared_drive_orgwide_analysis.py'), run_name='__main__')
        reports = list(Path(tmp, 'reports').glob('*_report_Team_Drive.csv'))
        trees = list(Path(tmp, 'reports').glob('*_tree_Team_Drive.txt'))
        self.assertEqual((len(reports), len(trees)), (1, 1))
        return g, reports[0], trees[0]


class SingleDriveScriptTest(FolderPathCsvMixin, unittest.TestCase):
    def run_script(self, tmp):
        with unittest.mock.patch.object(sys, 'argv', ['shared_drive_analysis.py', '--drive-id', DRIVE_ID]):
            g = runpy.run_path(str(REPO_ROOT / 'shared_drive_analysis.py'), run_name='__main__')
        return g, Path(tmp, 'report.csv'), Path(tmp, 'tree.txt')


if __name__ == '__main__':
    unittest.main()
