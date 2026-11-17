import builtins
import glob
import io
import ntpath
import os
import runpy
import sys
import tempfile
import types
import unittest
import unittest.mock
from contextlib import redirect_stdout

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "shared_drive_orgwide_analysis.py")
FOLDER = 'application/vnd.google-apps.folder'
PLANTED_REQUIREMENTS = "google-api-python-client\n"


class _Request:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeDriveService:
    def __init__(self, drives, files_by_drive):
        self._drives = drives
        self._files_by_drive = files_by_drive

    def drives(self):
        service = self

        class _Drives:
            def list(self, **kwargs):
                return _Request({'drives': service._drives})
        return _Drives()

    def files(self):
        service = self

        class _Files:
            def list(self, driveId, **kwargs):
                return _Request({'files': service._files_by_drive.get(driveId, [])})
        return _Files()


def _fake_modules(drive_service):
    google = types.ModuleType("google")
    oauth2 = types.ModuleType("google.oauth2")
    service_account = types.ModuleType("google.oauth2.service_account")
    service_account.Credentials = types.SimpleNamespace(
        from_service_account_file=lambda *a, **kw: object())
    oauth2.service_account = service_account
    google.oauth2 = oauth2

    googleapiclient = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *a, **kw: drive_service
    googleapiclient.discovery = discovery

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **kw: None

    tqdm_module = types.ModuleType("tqdm")
    tqdm_module.tqdm = lambda iterable, **kw: iterable

    return {
        "google": google,
        "google.oauth2": oauth2,
        "google.oauth2.service_account": service_account,
        "googleapiclient": googleapiclient,
        "googleapiclient.discovery": discovery,
        "dotenv": dotenv,
        "tqdm": tqdm_module,
    }


class OrgwideOutputPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.realpath(self._tmp.name)
        self._old_cwd = os.getcwd()
        os.chdir(self.workdir)
        self.addCleanup(self._restore)

        sa_file = os.path.join(self.workdir, "placeholder-service-account.json")
        with open(sa_file, "w") as f:
            f.write("{}")
        self._env = unittest.mock.patch.dict(os.environ, {"SERVICE_ACCOUNT_FILE": sa_file})
        self._env.start()
        self.addCleanup(self._env.stop)

        with open("requirements.txt", "w") as f:
            f.write(PLANTED_REQUIREMENTS)

        self.opened_for_write = []

    def _restore(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _windows_open(self, file, mode='r', *args, **kwargs):
        # Win32 collapses "." and ".." segments and treats "\" as a separator
        # before the filesystem is consulted, so intermediate components need
        # not exist. Emulate that so the test reflects a Windows operator.
        if isinstance(file, str):
            file = ntpath.normpath(file).replace("\\", "/")
        if any(flag in mode for flag in "wxa+"):
            self.opened_for_write.append(os.path.realpath(file))
        return builtins.open(file, mode, *args, **kwargs)

    def _run_scan(self, drives, files_by_drive):
        service = _FakeDriveService(drives, files_by_drive)
        with unittest.mock.patch.dict(sys.modules, _fake_modules(service)), \
                redirect_stdout(io.StringIO()) as out:
            runpy.run_path(SCRIPT, init_globals={"open": self._windows_open},
                           run_name="__main__")
        return out.getvalue()

    def _malicious_files(self):
        return [
            {'id': 'f1', 'name': 'attacker-package==6.6.6\n#', 'mimeType': FOLDER,
             'parents': ['drive-root']},
        ]

    def _assert_confined(self):
        reports = os.path.join(self.workdir, "reports")
        with open("requirements.txt") as f:
            self.assertEqual(f.read(), PLANTED_REQUIREMENTS)
        self.assertFalse(os.path.exists("requirements.csv"))
        for path in self.opened_for_write:
            self.assertEqual(os.path.dirname(path), reports,
                             f"report written outside reports/: {path}")

    def test_forward_slash_dot_dot_drive_name_stays_in_reports(self):
        drive_id = "0AAttackerDriveId"
        self._run_scan([{'id': drive_id, 'name': '/../../requirements'}],
                       {drive_id: self._malicious_files()})
        self._assert_confined()

    def test_backslash_dot_dot_drive_name_stays_in_reports(self):
        drive_id = "0AAttackerDriveId"
        self._run_scan([{'id': drive_id, 'name': '\\..\\..\\requirements'}],
                       {drive_id: self._malicious_files()})
        self._assert_confined()

    def test_unexpected_drive_id_is_not_used_as_a_path(self):
        self._run_scan([{'id': '../../requirements', 'name': 'Team'}],
                       {'../../requirements': self._malicious_files()})
        self._assert_confined()

    def test_regular_drive_reports_are_written_per_drive_id(self):
        files = [
            {'id': 'a', 'name': 'Alpha', 'mimeType': FOLDER, 'parents': ['root1']},
            {'id': 'b', 'name': 'doc.txt', 'mimeType': 'text/plain',
             'parents': ['a'], 'size': '10'},
        ]
        self._run_scan(
            [{'id': '0ADriveOne', 'name': 'Team Drive'},
             {'id': '0ADriveTwo', 'name': 'Team Drive'}],
            {'0ADriveOne': files, '0ADriveTwo': files})
        self._assert_confined()

        csvs = sorted(glob.glob("reports/*_report_*.csv"))
        trees = sorted(glob.glob("reports/*_tree_*.txt"))
        self.assertEqual(len(csvs), 2)
        self.assertEqual(len(trees), 2)
        self.assertTrue(csvs[0].endswith("_report_Team_Drive_0ADriveOne.csv"))
        self.assertTrue(trees[1].endswith("_tree_Team_Drive_0ADriveTwo.txt"))
        with open(csvs[0]) as f:
            self.assertIn("Alpha,1,10", f.read())
        with open(trees[0]) as f:
            self.assertEqual(f.read(), "Alpha/")

    def test_non_latin_drive_name_stays_in_the_filename(self):
        self._run_scan(
            [{'id': '0AEquipe', 'name': 'Équipe'},
             {'id': '0AJapanese', 'name': '営業部'}],
            {'0AEquipe': [], '0AJapanese': []})
        self._assert_confined()
        names = sorted(os.path.basename(p) for p in glob.glob("reports/*_report_*.csv"))
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].endswith("_report_Équipe_0AEquipe.csv"))
        self.assertTrue(names[1].endswith("_report_営業部_0AJapanese.csv"))


if __name__ == "__main__":
    unittest.main()
