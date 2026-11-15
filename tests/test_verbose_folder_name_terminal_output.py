"""Verbose single-drive output must not send folder names to the terminal raw.

shared_drive_analysis.py prints each tree line when --verbose is set. The
tree file keeps the folder name as the API returned it. The terminal line
drops C0/C1 controls, bidi controls, and line separators, and maps other
spaces to a normal space.
"""

import importlib
import io
import json
import os
import sys
import unittest
import urllib.parse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httplib2
from google.auth.credentials import AnonymousCredentials


REPO_ROOT = Path(__file__).resolve().parents[1]

HOSTILE_FOLDER = "Plans\u3000A\n✅ forged\r\x1b[2J\x7f\u202ehidden\u200d"


class ScriptedFilesHttp:
    def __init__(self, status, folder_name):
        self.status = status
        self.folder_name = folder_name
        self.uris = []

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        self.uris.append(uri)
        path = urllib.parse.urlparse(uri).path.rstrip("/")
        if not path.endswith("/files"):
            raise AssertionError("unexpected Drive API request: %s" % uri)
        if self.status != 200:
            content = json.dumps(
                {
                    "error": {
                        "code": self.status,
                        "message": "list failed\nforged\x1b[2J",
                    }
                }
            ).encode("utf-8")
            return httplib2.Response({"status": str(self.status)}), content
        query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
        drive_id = query["driveId"][0]
        payload = {
            "kind": "drive#fileList",
            "files": [
                {
                    "id": "folder-1",
                    "name": self.folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [drive_id],
                }
            ],
        }
        return httplib2.Response({"status": "200"}), json.dumps(payload).encode("utf-8")


@contextmanager
def patched_transport(http):
    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        return http.request(uri, method=method, body=body, headers=headers, **kwargs)

    with patch.object(httplib2.Http, "request", request), patch.object(
        __import__(
            "google.oauth2.service_account", fromlist=["Credentials"]
        ).Credentials,
        "from_service_account_file",
        return_value=AnonymousCredentials(),
    ):
        yield


def run_verbose_scanner(drive_id, folder_name, status=200):
    http = ScriptedFilesHttp(status, folder_name)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with TemporaryDirectory() as tmp:
        placeholder = Path(tmp) / "service-account.json"
        placeholder.write_text('{"note": "placeholder, not a credential"}\n', encoding="utf-8")
        csv_path = Path(tmp) / "out.csv"
        tree_path = Path(tmp) / "out.txt"
        env = os.environ.copy()
        env["SERVICE_ACCOUNT_FILE"] = str(placeholder)
        env["OUTPUT_CSV"] = str(csv_path)
        env["OUTPUT_TREE"] = str(tree_path)
        previous = os.getcwd()
        previous_argv = sys.argv
        os.chdir(tmp)
        sys.argv = [
            "shared_drive_analysis.py",
            "--drive-id",
            drive_id,
            "--verbose",
        ]
        sys.modules.pop("shared_drive_analysis", None)
        raised = None
        try:
            with patched_transport(http), redirect_stdout(stdout), redirect_stderr(
                stderr
            ), patch.dict(os.environ, env, clear=False):
                sys.path.insert(0, str(REPO_ROOT))
                try:
                    importlib.import_module("shared_drive_analysis")
                except Exception as exc:
                    raised = exc
            # newline="" keeps CR from the folder name. Text mode would turn it into LF.
            if tree_path.is_file():
                with open(tree_path, encoding="utf-8", newline="") as handle:
                    tree_text = handle.read()
            else:
                tree_text = ""
            if csv_path.is_file():
                with open(csv_path, encoding="utf-8", newline="") as handle:
                    csv_text = handle.read()
            else:
                csv_text = ""
        finally:
            os.chdir(previous)
            sys.argv = previous_argv
            sys.modules.pop("shared_drive_analysis", None)
            try:
                sys.path.remove(str(REPO_ROOT))
            except ValueError:
                # REPO_ROOT was not inserted, or it was already removed.
                pass
    return stdout.getvalue(), stderr.getvalue(), tree_text, csv_text, raised


class VerboseFolderNameTerminalOutputTest(unittest.TestCase):
    def test_verbose_tree_line_is_sanitized_and_tree_file_is_not(self):
        stdout, stderr, tree_text, csv_text, raised = run_verbose_scanner(
            "drive-id-1", HOSTILE_FOLDER
        )
        self.assertIsNone(raised)
        self.assertIn(HOSTILE_FOLDER + "/", tree_text)
        self.assertIn(HOSTILE_FOLDER, csv_text)

        printed = "Plans A✅ forged[2Jhidden\u200d/"
        self.assertIn(printed, stdout.splitlines())
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\r", stdout)
        self.assertNotIn("\x7f", stdout)
        self.assertNotIn("\u202e", stdout)
        self.assertNotIn("\u3000", stdout)
        # The folder name's newline must not become its own status line.
        self.assertNotIn("✅ forged", "\n".join(
            line for line in stdout.splitlines() if line != printed
        ))
        self.assertNotIn("\x1b", stderr)
        self.assertNotIn("\u202e", stderr)

    def test_files_list_failure_propagates_without_a_forged_status_line(self):
        stdout, stderr, tree_text, csv_text, raised = run_verbose_scanner(
            "drive-id-err", HOSTILE_FOLDER, status=500
        )
        self.assertIsNotNone(raised)
        self.assertIn("list failed", str(raised))
        self.assertEqual(tree_text, "")
        self.assertEqual(csv_text, "")
        # The failure happens before any folder name is printed.
        self.assertNotIn("Plans", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("✅ forged", stdout)
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
