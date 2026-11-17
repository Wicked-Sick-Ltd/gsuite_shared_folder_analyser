import csv
import os
import posixpath
import runpy
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER = 'application/vnd.google-apps.folder'
DRIVE_ID = 'drive-1'


def folder(file_id, name, parent):
    return {
        'id': file_id, 'name': name, 'mimeType': FOLDER, 'parents': [parent],
        'modifiedTime': '2026-01-01T00:00:00Z',
        'owners': [{'emailAddress': f'{file_id}@example.com'}],
    }


def document(file_id, parent):
    return {
        'id': file_id, 'name': f'{file_id}.txt', 'mimeType': 'text/plain',
        'parents': [parent], 'size': '10', 'modifiedTime': '2026-01-01T00:00:00Z',
    }


# Expected decoded path for every folder, keyed by Drive id.
EXPECTED_PATHS = {
    'finance': ['Finance'],
    'payroll': ['Finance', 'Payroll'],
    'dotdot': ['Finance', 'Payroll', '..'],
    'dot': ['Finance', 'Payroll', '.'],
    'dotdot-child': ['Finance', 'Payroll', '..', 'Archive'],
    'slashed': ['Finance/Payroll'],
    'backslashed': ['Finance\\..'],
    'newline': ['Public\nFinance'],
    'indented': ['  Spoof'],
    'percent': ['%2E%2E'],
    'empty': ['Finance', ''],
}

FILES = [
    folder('finance', 'Finance', DRIVE_ID),
    folder('payroll', 'Payroll', 'finance'),
    folder('dotdot', '..', 'payroll'),
    folder('dot', '.', 'payroll'),
    folder('dotdot-child', 'Archive', 'dotdot'),
    folder('slashed', 'Finance/Payroll', DRIVE_ID),
    folder('backslashed', 'Finance\\..', DRIVE_ID),
    folder('newline', 'Public\nFinance', DRIVE_ID),
    folder('indented', '  Spoof', DRIVE_ID),
    folder('percent', '%2E%2E', DRIVE_ID),
    folder('empty', '', 'finance'),
    document('doc-in-dotdot', 'dotdot'),
    document('doc-in-dot', 'dot'),
]


class FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class FakeDriveService:
    def drives(self):
        return types.SimpleNamespace(
            list=lambda **kwargs: FakeRequest({'drives': [{'id': DRIVE_ID, 'name': 'Team'}]})
        )

    def files(self):
        return types.SimpleNamespace(list=lambda **kwargs: FakeRequest({'files': FILES}))


def stub_modules():
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
    discovery.build = lambda *args, **kwargs: FakeDriveService()
    googleapiclient.discovery = discovery
    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *args, **kwargs: None
    tqdm = types.ModuleType('tqdm')
    tqdm.tqdm = lambda iterable, **kwargs: iterable
    return {
        'google': google,
        'google.oauth2': oauth2,
        'google.oauth2.service_account': service_account,
        'googleapiclient': googleapiclient,
        'googleapiclient.discovery': discovery,
        'dotenv': dotenv,
        'tqdm': tqdm,
    }


class ReportPathTestMixin:
    script = None

    def run_script(self, workdir):
        raise NotImplementedError

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        credentials = self.workdir / 'service-account-placeholder'
        credentials.write_text('placeholder')
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(self._tmp.cleanup)
        os.chdir(self.workdir)
        env = {'SERVICE_ACCOUNT_FILE': str(credentials)}
        with unittest.mock.patch.dict(sys.modules, stub_modules()), \
                unittest.mock.patch.dict(os.environ, env), \
                unittest.mock.patch.object(sys, 'path', [str(REPO_ROOT)] + sys.path), \
                unittest.mock.patch('builtins.print'):
            self.csv_path, self.tree_path = self.run_script(self.workdir)
        with open(self.csv_path, newline='', encoding='utf-8') as f:
            self.rows = list(csv.DictReader(f))
        self.tree_lines = self.tree_path.read_text(encoding='utf-8').split('\n')

    def test_every_folder_is_reported(self):
        self.assertEqual(len(self.rows), len(EXPECTED_PATHS))

    def test_csv_paths_survive_normalization(self):
        for row in self.rows:
            path = row['Folder Path']
            segments = path.split('/')
            with self.subTest(path=path):
                self.assertTrue(all(segments), path)
                self.assertNotIn('.', segments)
                self.assertNotIn('..', segments)
                self.assertNotIn('\\', path)
                self.assertEqual(posixpath.normpath(path), path)

    def test_csv_paths_decode_to_drive_names(self):
        by_owner = {row['Owner Email']: row['Folder Path'] for row in self.rows}
        for file_id, expected in EXPECTED_PATHS.items():
            path = by_owner[f'{file_id}@example.com']
            with self.subTest(file_id=file_id):
                decoded = [unquote(s) if s != '%00' else '' for s in path.split('/')]
                self.assertEqual(decoded, expected)

    def test_files_inside_dot_folders_stay_with_their_folder(self):
        by_owner = {row['Owner Email']: row for row in self.rows}
        dotdot = by_owner['dotdot@example.com']
        dot = by_owner['dot@example.com']
        self.assertEqual(posixpath.normpath(dotdot['Folder Path']), 'Finance/Payroll/%2E%2E')
        self.assertEqual(posixpath.normpath(dot['Folder Path']), 'Finance/Payroll/%2E')
        self.assertEqual(dotdot['File Count'], '1')
        self.assertEqual(dot['File Count'], '1')

    def test_tree_has_one_line_per_folder_without_reserved_names(self):
        self.assertEqual(len(self.tree_lines), len(EXPECTED_PATHS))
        for line in self.tree_lines:
            with self.subTest(line=line):
                self.assertTrue(line.endswith('/'))
                body = line[:-1]
                name = body.lstrip(' ')
                indent = len(body) - len(name)
                self.assertEqual(indent % 2, 0)
                self.assertNotIn(name, ('', '.', '..'))
                self.assertNotIn('/', name)
                self.assertNotIn('\\', name)
                self.assertFalse(name.startswith(' '))

    def test_tree_nests_dot_folders_under_their_real_parent(self):
        self.assertIn('    %2E%2E/', self.tree_lines)
        self.assertIn('    %2E/', self.tree_lines)
        self.assertIn('      Archive/', self.tree_lines)


class EncodePathSegmentTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO_ROOT))
        self.addCleanup(sys.path.remove, str(REPO_ROOT))
        from drive_paths import encode_path_segment
        self.encode = encode_path_segment

    def test_ordinary_names_are_unchanged(self):
        for name in ('Finance', 'Q1 Reports', 'v1.2', '.hidden', 'Café', 'a..b'):
            with self.subTest(name=name):
                self.assertEqual(self.encode(name), name)

    def test_names_that_normalize_away_are_encoded(self):
        names = ('.', '..', '...', ' ', '. .', ' ..', '\uff0e\uff0e', '\u2024\u2024', '')
        for name in names:
            encoded = self.encode(name)
            with self.subTest(name=name, encoded=encoded):
                self.assertTrue(encoded)
                self.assertTrue(set(encoded) - {'.', ' '})
                self.assertEqual(posixpath.normpath(f'a/{encoded}'), f'a/{encoded}')
                if name:
                    self.assertEqual(unquote(encoded), name)

    def test_separators_and_line_breaks_are_encoded(self):
        names = ('a/b', 'a\\b', 'a\nb', 'a\rb', 'a\u2028b', 'a\x85b', '\uff0fetc', '50%', '  x')
        for name in names:
            encoded = self.encode(name)
            with self.subTest(name=name, encoded=encoded):
                self.assertNotIn('/', encoded)
                self.assertNotIn('\\', encoded)
                self.assertEqual(len(f'x{encoded}x'.splitlines()), 1)
                self.assertFalse(encoded.startswith(' '))
                self.assertEqual(unquote(encoded), name)

    def test_encoded_names_do_not_collide_with_literal_names(self):
        self.assertNotEqual(self.encode('..'), self.encode('%2E%2E'))
        self.assertNotEqual(self.encode(''), self.encode('%00'))

    def test_empty_name_and_nul_share_one_token(self):
        self.assertEqual(self.encode(''), '%00')
        self.assertEqual(self.encode('\x00'), '%00')
        self.assertEqual(unquote('%00'), '\x00')


class SingleDriveScriptTest(ReportPathTestMixin, unittest.TestCase):
    def run_script(self, workdir):
        csv_path = workdir / 'report.csv'
        tree_path = workdir / 'tree.txt'
        env = {'OUTPUT_CSV': str(csv_path), 'OUTPUT_TREE': str(tree_path)}
        argv = ['shared_drive_analysis.py', '--drive-id', DRIVE_ID]
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(sys, 'argv', argv):
            runpy.run_path(str(REPO_ROOT / 'shared_drive_analysis.py'), run_name='__main__')
        return csv_path, tree_path


class OrgWideScriptTest(ReportPathTestMixin, unittest.TestCase):
    def run_script(self, workdir):
        runpy.run_path(str(REPO_ROOT / 'shared_drive_orgwide_analysis.py'), run_name='__main__')
        reports = workdir / 'reports'
        (csv_path,) = reports.glob('*_report_Team.csv')
        (tree_path,) = reports.glob('*_tree_Team.txt')
        return csv_path, tree_path


if __name__ == '__main__':
    unittest.main()
