"""Org-wide scanner must not send shared-drive titles to the terminal raw.

Drive titles are organizer-controlled strings returned by drives.list. The
scanner prints each title when a drive starts, in the tqdm description, and
again on success or error. Control characters, bidi controls, and line
separators are removed before any of those writes. Other spaces become
underscores. The drive id is logged beside the label, and report files include
that id so stripped characters cannot make two drives share a path.
"""

import importlib
import io
import json
import os
import re
import sys
import unicodedata
import unittest
import urllib.parse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httplib2
from google.auth.credentials import AnonymousCredentials


REPO_ROOT = Path(__file__).resolve().parents[1]

_BIDI = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def terminal_label(name):
    """Console label: drop terminal controls, keep other letters, spaces to _."""
    kept = []
    for ch in name:
        if unicodedata.category(ch) == "Cc" or ch in _BIDI or ch in "\u2028\u2029":
            continue
        if unicodedata.category(ch) == "Zs":
            kept.append("_")
        else:
            kept.append(ch)
    return "".join(kept)


def report_token(drive_id):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(drive_id))
    return safe or "unknown"


def done_line(label, drive_id):
    """Success line once the title is sanitized and the drive id is on the path."""
    file_id = report_token(drive_id)
    file_label = f"{label}_{file_id}" if label else file_id
    escaped_label = re.escape(label)
    escaped_id = re.escape(drive_id)
    escaped_file = re.escape(file_label)
    return re.compile(
        r"^✅ Done: %s \(%s\) → reports/\d{8}_report_%s\.csv, reports/\d{8}_tree_%s\.txt$"
        % (escaped_label, escaped_id, escaped_file, escaped_file)
    )


class ScriptedDriveHttp:
    """Stand-in for the network only. Response bytes are parsed by the real client."""

    def __init__(self, pages, file_status_by_drive, file_name_by_drive=None, error_message="list failed"):
        self.pages = pages
        self.page_index = 0
        self.file_status_by_drive = file_status_by_drive
        self.file_name_by_drive = file_name_by_drive or {}
        self.error_message = error_message
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
                    {"error": {"code": status, "message": self.error_message}}
                ).encode("utf-8")
                return httplib2.Response({"status": str(status)}), content
            payload = {
                "kind": "drive#fileList",
                "files": [
                    {
                        "id": "folder-%s" % drive_id,
                        "name": self.file_name_by_drive.get(drive_id, "Plans"),
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


def run_orgwide_scanner(pages, file_status_by_drive, file_name_by_drive=None, error_message="list failed"):
    """Import the scanner so its top-level loop runs against scripted responses."""
    http = ScriptedDriveHttp(
        pages, file_status_by_drive, file_name_by_drive, error_message
    )
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
        reports = {}
        try:
            with patched_transport(http), redirect_stdout(stdout), redirect_stderr(
                stderr
            ), patch.dict(os.environ, env, clear=False):
                sys.path.insert(0, str(REPO_ROOT))
                importlib.import_module("shared_drive_orgwide_analysis")
            reports_dir = Path(tmp) / "reports"
            if reports_dir.is_dir():
                for path in reports_dir.iterdir():
                    reports[path.name] = path.read_text(encoding="utf-8")
            outside = [
                str(path.relative_to(tmp))
                for path in Path(tmp).rglob("*")
                if path.is_file() and path.name != "service-account.json" and "reports" not in path.parts
            ]
        finally:
            os.chdir(previous)
            sys.modules.pop("shared_drive_orgwide_analysis", None)
            try:
                sys.path.remove(str(REPO_ROOT))
            except ValueError:
                pass
    return stdout.getvalue(), stderr.getvalue(), http.uris, reports, outside


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
        stdout, stderr, uris, reports, outside = run_orgwide_scanner(
            [[{"id": drive_id, "name": HOSTILE_NAME}]],
            {},
        )
        self.assertEqual(outside, [])
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

        file_label = "%s_%s" % (label, drive_id)
        csv_names = [name for name in reports if name.endswith(".csv")]
        tree_names = [name for name in reports if name.endswith(".txt")]
        self.assertEqual(len(csv_names), 1, reports)
        self.assertEqual(len(tree_names), 1, reports)
        self.assertIn(file_label, csv_names[0])
        self.assertIn(file_label, tree_names[0])

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
        error_message = "list failed\n✅ Done: forged\r\x1b[2J\u202ehidden"
        stdout, stderr, uris, reports, outside = run_orgwide_scanner(
            [[{"id": drive_id, "name": HOSTILE_NAME}]],
            {drive_id: 500},
            error_message=error_message,
        )
        self.assertEqual(outside, [])
        self.assertEqual(reports, {})
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
        self.assertIn("Done: forged", lines[1])
        self.assertIn("hidden", lines[1])
        self.assertNotIn("\u202e", lines[1])
        self.assertNotIn("\r", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\x7f", stdout)
        self.assertNotIn("\x08", stdout)
        self.assertNotIn("\x0b", stdout)
        for line in lines:
            self.assertTrue(all(unicodedata.category(ch) != "Cc" for ch in line), line)
        # The file list fails before the progress bar is created.
        self.assertEqual(stderr, "")

    def test_second_page_title_is_sanitized_too(self):
        first_id = "drive-id-page-1"
        second_id = "drive-id-page-2"
        stdout, stderr, uris, reports, outside = run_orgwide_scanner(
            [
                [{"id": first_id, "name": "Finance Team"}],
                [{"id": second_id, "name": HOSTILE_NAME}],
            ],
            {},
        )
        self.assertEqual(outside, [])
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
        csv_names = sorted(name for name in reports if name.endswith(".csv"))
        self.assertEqual(len(csv_names), 2, reports)
        self.assertNotEqual(csv_names[0], csv_names[1])

    def test_names_that_differ_by_stripped_characters_keep_separate_reports(self):
        plain_id = "id-plain"
        esc_id = "id-esc"
        blank_id = "id-blank"
        blank_id_2 = "id/blank"
        stdout, stderr, uris, reports, outside = run_orgwide_scanner(
            [[
                {"id": plain_id, "name": "Finance"},
                {"id": esc_id, "name": "Finance\x1b"},
                {"id": blank_id, "name": "\x1b\n"},
                {"id": blank_id_2, "name": "\r\x7f"},
            ]],
            {},
            file_name_by_drive={
                plain_id: "Plain Folder",
                esc_id: "Esc Folder",
                blank_id: "Blank Folder",
                blank_id_2: "Slash Id Folder",
            },
        )
        self.assertEqual(outside, [])
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 8, stdout)
        self.assertEqual(lines[0], "🔍 Processing Shared Drive: Finance (%s)" % plain_id)
        self.assertEqual(lines[2], "🔍 Processing Shared Drive: Finance (%s)" % esc_id)
        self.assertEqual(lines[4], "🔍 Processing Shared Drive:  (%s)" % blank_id)
        self.assertEqual(lines[6], "🔍 Processing Shared Drive:  (%s)" % blank_id_2)

        csv_names = sorted(name for name in reports if name.endswith(".csv"))
        tree_names = sorted(name for name in reports if name.endswith(".txt"))
        self.assertEqual(len(csv_names), 4, reports)
        self.assertEqual(len(tree_names), 4, reports)
        self.assertEqual(len(set(csv_names)), 4)
        for name in list(reports):
            self.assertNotIn("/", name)
            self.assertNotIn("..", name)
        self.assertTrue(any(name.endswith("_report_Finance_%s.csv" % plain_id) for name in csv_names))
        self.assertTrue(any(name.endswith("_report_Finance_%s.csv" % esc_id) for name in csv_names))
        self.assertTrue(any(name.endswith("_report_%s.csv" % blank_id) for name in csv_names))
        self.assertTrue(any(name.endswith("_report_id_blank.csv") for name in csv_names))
        by_folder = {
            "Plain Folder": plain_id,
            "Esc Folder": esc_id,
            "Blank Folder": blank_id,
            "Slash Id Folder": "id_blank",
        }
        for folder, token in by_folder.items():
            matches = [text for name, text in reports.items() if name.endswith(".csv") and token in name]
            self.assertEqual(len(matches), 1, reports)
            self.assertIn(folder, matches[0])
        self.assertNotIn("\x1b", stdout)
        self.assertNotIn("\x1b", stderr)

    def test_ordinary_spaces_and_joiners_stay_visible(self):
        drive_id = "id-ordinary"
        name = "営業\u3000部 \u00a0family\u200dname well\u00adknown\u2028\u202eX"
        stdout, stderr, uris, reports, outside = run_orgwide_scanner(
            [[{"id": drive_id, "name": name}]],
            {},
        )
        self.assertEqual(outside, [])
        self.assertTrue(any("/drives" in uri for uri in uris))
        label = terminal_label(name)
        self.assertEqual(label, "営業_部__family\u200dname_well\u00adknownX")
        self.assertIn("\u200d", label)
        self.assertIn("\u00ad", label)
        self.assertNotIn("\u2028", label)
        self.assertNotIn("\u202e", label)
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 2, stdout)
        self.assertEqual(
            lines[0], "🔍 Processing Shared Drive: %s (%s)" % (label, drive_id)
        )
        self.assertRegex(lines[1], done_line(label, drive_id))
        csv_names = [report_name for report_name in reports if report_name.endswith(".csv")]
        self.assertEqual(len(csv_names), 1, reports)
        self.assertIn(label, csv_names[0])
        self.assertIn(drive_id, csv_names[0])
        self.assertNotIn("\u2028", stderr)
        self.assertNotIn("\u202e", stderr)


if __name__ == "__main__":
    unittest.main()
