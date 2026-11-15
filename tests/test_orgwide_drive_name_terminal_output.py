"""Org-wide scanner must not send shared-drive titles to the terminal raw.

Drive titles are organizer-controlled strings returned by drives.list. The
scanner prints each title when a drive starts, in the tqdm description, and
again on success or error. These tests are the expected behavior: control
characters are removed before any of those writes, spaces become underscores,
and the drive id is logged beside the label. They fail while the current
code writes the title through unchanged apart from spaces.
"""

import importlib
import io
import json
import os
import re
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


def terminal_label(name):
    """Label safe to write to a terminal. Spec for the scanner's display name."""
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in "\x7f")
    return cleaned.replace(" ", "_")


def done_line(label, drive_id):
    """Success line once the title is sanitized and the drive id is logged beside it."""
    escaped_label = re.escape(label)
    return re.compile(
        r"^✅ Done: %s \(%s\) → reports/\d{8}_report_%s\.csv, reports/\d{8}_tree_%s\.txt$"
        % (escaped_label, re.escape(drive_id), escaped_label, escaped_label)
    )


class ScriptedDriveHttp:
    """Stand-in for the network only. Response bytes are parsed by the real client."""

    def __init__(self, pages, file_status_by_drive):
        self.pages = pages
        self.page_index = 0
        self.file_status_by_drive = file_status_by_drive
        self.uris = []

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        self.uris.append(uri)
        path = urllib.parse.urlparse(uri).path.rstrip("/")
        if path.endswith("/drives"):
            drives = self.pages[self.page_index]
            self.page_index += 1
            payload = {"kind": "drive#driveList", "drives": drives}
            if self.page_index < len(self.pages):
                payload["nextPageToken"] = "next-page"
            content = json.dumps(payload).encode("utf-8")
            return httplib2.Response({"status": "200"}), content
        if path.endswith("/files"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
            drive_id = query["driveId"][0]
            status = self.file_status_by_drive.get(drive_id, 200)
            if status != 200:
                content = json.dumps(
                    {"error": {"code": status, "message": "list failed"}}
                ).encode("utf-8")
                return httplib2.Response({"status": str(status)}), content
            payload = {
                "kind": "drive#fileList",
                "files": [
                    {
                        "id": "folder-1",
                        "name": "Plans",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": [drive_id],
                    }
                ],
            }
            return httplib2.Response({"status": "200"}), json.dumps(payload).encode(
                "utf-8"
            )
        raise AssertionError("unexpected Drive API request: %s" % uri)


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


def run_orgwide_scanner(pages, file_status_by_drive):
    """Import the scanner so its top-level loop runs against scripted responses."""
    http = ScriptedDriveHttp(pages, file_status_by_drive)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with TemporaryDirectory() as tmp:
        placeholder = Path(tmp) / "service-account.json"
        placeholder.write_text('{"note": "placeholder, not a credential"}\n', encoding="utf-8")
        env = os.environ.copy()
        env["SERVICE_ACCOUNT_FILE"] = str(placeholder)
        previous = os.getcwd()
        os.chdir(tmp)
        sys.modules.pop("shared_drive_orgwide_analysis", None)
        try:
            with patched_transport(http), redirect_stdout(stdout), redirect_stderr(
                stderr
            ), patch.dict(os.environ, env, clear=False):
                sys.path.insert(0, str(REPO_ROOT))
                importlib.import_module("shared_drive_orgwide_analysis")
        finally:
            os.chdir(previous)
            sys.modules.pop("shared_drive_orgwide_analysis", None)
            try:
                sys.path.remove(str(REPO_ROOT))
            except ValueError:
                pass
    return stdout.getvalue(), stderr.getvalue(), http.uris


# Newline, carriage return, ESC, DEL, backspace, and vertical tab.
# Slashes are left out of the title so a success-path run reaches the Done line
# on Linux. The controls under test are newline, carriage return, ESC, DEL,
# backspace, and vertical tab.
HOSTILE_NAME = (
    "Café Review\n"
    "✅ Done: other_drive → forged.csv, forged.txt\r"
    "EVIL\x1b]0;renamed\x07\x7f\x08\x0b"
)


class OrgwideDriveNameTerminalOutputTest(unittest.TestCase):
    def test_success_and_progress_use_sanitized_label_and_drive_id(self):
        drive_id = "drive-id-1"
        stdout, stderr, uris = run_orgwide_scanner(
            [[{"id": drive_id, "name": HOSTILE_NAME}]],
            {},
        )
        self.assertTrue(any("/drives" in uri for uri in uris))
        self.assertTrue(any("driveId=" in uri and drive_id in uri for uri in uris))

        label = terminal_label(HOSTILE_NAME)
        self.assertNotIn("\n", label)
        self.assertIn("Café_Review", label)

        lines = stdout.splitlines()
        self.assertEqual(len(lines), 2, stdout)
        self.assertEqual(
            lines[0], "🔍 Processing Shared Drive: %s (%s)" % (label, drive_id)
        )
        self.assertRegex(lines[1], done_line(label, drive_id))
        self.assertNotIn("\r", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\x7f", stdout)
        self.assertNotIn("\x08", stdout)
        self.assertNotIn("\x0b", stdout)
        for line in lines:
            self.assertTrue(all(ch.isprintable() for ch in line), line)

        # tqdm frames the bar with CR and closes it with one newline.
        # A CR or newline from the title shows up as an extra record.
        self.assertEqual(stderr.count("\n"), 1, stderr)
        records = [part for part in stderr.split("\r") if part]
        self.assertTrue(records, stderr)
        for record in records:
            text = record.rstrip("\n")
            self.assertTrue(text.startswith("Analyzing %s" % label), stderr)
            self.assertTrue(all(ch.isprintable() for ch in text), stderr)
        self.assertNotIn("\x1b", stderr)
        self.assertNotIn("\x7f", stderr)
        self.assertNotIn("\x08", stderr)

    def test_error_line_uses_sanitized_label_and_drive_id(self):
        drive_id = "drive-id-err"
        stdout, stderr, uris = run_orgwide_scanner(
            [[{"id": drive_id, "name": HOSTILE_NAME}]],
            {drive_id: 500},
        )
        self.assertTrue(any("/drives" in uri for uri in uris))
        self.assertTrue(any("/files" in uri and drive_id in uri for uri in uris))

        label = terminal_label(HOSTILE_NAME)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 2, stdout)
        self.assertEqual(
            lines[0], "🔍 Processing Shared Drive: %s (%s)" % (label, drive_id)
        )
        self.assertTrue(
            lines[1].startswith("❌ Error processing %s (%s): " % (label, drive_id)),
            lines[1],
        )
        self.assertIn("list failed", lines[1])
        self.assertNotIn("\r", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\x7f", stdout)
        self.assertNotIn("\x08", stdout)
        self.assertNotIn("\x0b", stdout)
        for line in lines:
            self.assertTrue(all(ch.isprintable() for ch in line), line)
        # The file list fails before the progress bar is created.
        self.assertEqual(stderr, "")

    def test_second_page_title_is_sanitized_too(self):
        first_id = "drive-id-page-1"
        second_id = "drive-id-page-2"
        stdout, stderr, uris = run_orgwide_scanner(
            [
                [{"id": first_id, "name": "Finance Team"}],
                [{"id": second_id, "name": HOSTILE_NAME}],
            ],
            {},
        )
        drive_calls = [uri for uri in uris if "/drives" in uri and "/files" not in uri]
        self.assertEqual(len(drive_calls), 2)
        self.assertIn("pageToken=next-page", drive_calls[1])

        first = terminal_label("Finance Team")
        second = terminal_label(HOSTILE_NAME)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 4, stdout)
        self.assertEqual(
            lines[0], "🔍 Processing Shared Drive: %s (%s)" % (first, first_id)
        )
        self.assertRegex(lines[1], done_line(first, first_id))
        self.assertEqual(
            lines[2], "🔍 Processing Shared Drive: %s (%s)" % (second, second_id)
        )
        self.assertRegex(lines[3], done_line(second, second_id))
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\r", stdout)
        self.assertEqual(stderr.count("\n"), 2, stderr)
        self.assertNotIn("\x1b", stderr)


if __name__ == "__main__":
    unittest.main()
