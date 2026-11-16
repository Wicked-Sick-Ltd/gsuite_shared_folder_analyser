"""Folder names that contain tab characters must not change tree depth.

Both scanners indent a folder with two spaces per parent link, then append
the Drive folder name unchanged:

- shared_drive_orgwide_analysis.print_tree writes that line into the tree file
- shared_drive_analysis.print_tree writes the same line into the tree file and,
  with --verbose, prints it

A terminal expands a tab to the next 8-column stop (GNU expand -t 8). A name
that starts with a tab is therefore painted in a deeper column than the
parent links justify, and a tab later in the name shifts the rest of the name
to a later stop.

These tests fail while that raw name is written. They pass when each tab in a
folder name is deleted or replaced by one or two non-whitespace characters
before the line is built, the line does not start the name with whitespace,
and the two-space indent is still exactly the depth of the parent chain.
Replacing a tab with spaces, or calling expandtabs on the name, keeps the
forged indent and must still fail.

Depth is taken from parent links, not from whitespace inside the name.

Not covered here, because they do not encode depth as a space prefix:
- get_folder_path in both scripts (slash-joined CSV path)
- the shared-drive title used only as the report filename
File names are not listed in the tree, so a tab in a non-folder name is also
untested. Newline characters in names are a different bug and are untested.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from googleapiclient.model import JsonModel

REPO = Path(__file__).resolve().parents[1]
FOLDER = "application/vnd.google-apps.folder"
DRIVE_ID = "drv1"
ORGWIDE_SCRIPT = REPO / "shared_drive_orgwide_analysis.py"
SINGLE_SCRIPT = REPO / "shared_drive_analysis.py"

# JSON object bytes, the shape files.list / drives.list return after
# JsonModel.deserialize (googleapiclient 2.202.0 model.py).
FILES = [
    {"id": "projects", "name": "Projects", "mimeType": FOLDER, "parents": [DRIVE_ID]},
    {"id": "finance", "name": "Finance", "mimeType": FOLDER, "parents": ["projects"]},
    {"id": "notes", "name": "Notes", "mimeType": FOLDER, "parents": ["finance"]},
    {
        "id": "actuals",
        "name": "Q1\tActuals",
        "mimeType": FOLDER,
        "parents": ["finance"],
    },
    {"id": "spaced", "name": "Team Files", "mimeType": FOLDER, "parents": ["projects"]},
    {"id": "evil", "name": "\tFinance", "mimeType": FOLDER, "parents": [DRIVE_ID]},
    {"id": "payroll", "name": "Payroll", "mimeType": FOLDER, "parents": ["evil"]},
    {"id": "archive", "name": "\t\tArchive", "mimeType": FOLDER, "parents": [DRIVE_ID]},
]
DRIVES = [{"id": DRIVE_ID, "name": "Ops Drive"}]


def scanner_entries(files):
    """Parent-chain depth and visit order used by both print_tree callers."""
    file_map = {item["id"]: item for item in files}
    children = defaultdict(list)
    for item in files:
        for parent in item.get("parents", []):
            children[parent].append(item)
    folders = [item for item in files if item["mimeType"] == FOLDER]
    entries = []

    def walk(node, depth):
        entries.append((depth, node["name"]))
        for child in sorted(children.get(node["id"], []), key=lambda item: item["name"]):
            if child["mimeType"] == FOLDER:
                walk(child, depth + 1)

    for folder in sorted(folders, key=lambda item: item["name"]):
        if not any(parent in file_map for parent in folder.get("parents", [])):
            walk(folder, 0)
    return entries


def drive_json_response(payload: dict) -> dict:
    """Run the client JSON model the scanners' execute() postproc uses."""
    model = JsonModel(data_wrapper=False)
    raw = json.dumps(payload).encode("utf-8")
    response = SimpleNamespace(status=200)
    decoded = model.response(response, raw)
    if not isinstance(decoded, dict):
        raise AssertionError(f"JsonModel.response returned {type(decoded).__name__}")
    return decoded


def terminal_expand(text: str) -> str:
    """POSIX tab stops every 8 columns, the expansion a terminal applies."""
    completed = subprocess.run(
        ["expand", "-t", "8"],
        input=text.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8")


def first_content_column(line: str) -> int:
    expanded = terminal_expand(line)
    return len(expanded) - len(expanded.lstrip(" "))


def tabs_neutralized(label: str, name: str) -> bool:
    """True when each tab was deleted or replaced by 1 or 2 non-whitespace chars."""

    def rec(label_index: int, name_index: int) -> bool:
        if name_index == len(name):
            return label_index == len(label)
        character = name[name_index]
        if character != "\t":
            return (
                label_index < len(label)
                and label[label_index] == character
                and rec(label_index + 1, name_index + 1)
            )
        if rec(label_index, name_index + 1):
            return True
        for width in (1, 2):
            end = label_index + width
            if end > len(label):
                break
            chunk = label[label_index:end]
            if any(char.isspace() for char in chunk):
                break
            if rec(end, name_index + 1):
                return True
        return False

    return rec(0, 0)


def line_matches_depth(line: str, depth: int, name: str) -> bool:
    if "\t" in line or "\n" in line or "\r" in line:
        return False
    if terminal_expand(line) != line:
        return False
    prefix = "  " * depth
    if not line.startswith(prefix):
        return False
    rest = line[len(prefix) :]
    if not rest.endswith("/"):
        return False
    label = rest[:-1]
    if label[:1].isspace():
        return False
    return tabs_neutralized(label, name)


def assert_tree_matches_parents(testcase: unittest.TestCase, lines, entries) -> None:
    testcase.assertEqual(
        len(lines),
        len(entries),
        "tree line count != folders reached from parent links\n"
        + "\n".join(repr(terminal_expand(line)) for line in lines),
    )
    used = [False] * len(lines)

    def search(index: int) -> bool:
        if index == len(entries):
            return True
        depth, name = entries[index]
        for line_index, line in enumerate(lines):
            if used[line_index]:
                continue
            if line_matches_depth(line, depth, name):
                used[line_index] = True
                if search(index + 1):
                    return True
                used[line_index] = False
        return False

    if search(0):
        return
    rendered = "\n".join(
        f"  raw={line!r} expanded={terminal_expand(line)!r} "
        f"content_column={first_content_column(line)}"
        for line in lines
    )
    expected = "\n".join(f"  depth={depth} name={name!r}" for depth, name in entries)
    testcase.fail(
        "tree lines do not show parent-chain depth once tabs are expanded:\n"
        f"{rendered}\nexpected one honest line each for:\n{expected}"
    )


RUNNER = textwrap.dedent(
    """
    import json, os, sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from googleapiclient.model import JsonModel
    import google.oauth2.service_account as service_account
    import googleapiclient.discovery as discovery

    model = JsonModel(data_wrapper=False)
    response = SimpleNamespace(status=200)
    files_payload = model.response(response, os.environ["TEST_FILES_JSON"].encode("utf-8"))
    drives_payload = model.response(response, os.environ["TEST_DRIVES_JSON"].encode("utf-8"))

    service = MagicMock()
    service.drives.return_value.list.return_value.execute.return_value = drives_payload
    service.files.return_value.list.return_value.execute.return_value = files_payload
    service_account.Credentials.from_service_account_file = MagicMock(return_value=MagicMock())
    discovery.build = MagicMock(return_value=service)

    sys.argv = json.loads(os.environ["TEST_ARGV"])
    import runpy
    runpy.run_path(os.environ["TEST_SCRIPT"], run_name="__main__")
    """
)


def run_scanner(script: Path, argv: list[str], workdir: Path, files_json: str, drives_json: str):
    env = os.environ.copy()
    env["SERVICE_ACCOUNT_FILE"] = str(workdir / "service-account-placeholder.json")
    env["TEST_FILES_JSON"] = files_json
    env["TEST_DRIVES_JSON"] = drives_json
    env["TEST_ARGV"] = json.dumps(argv)
    env["TEST_SCRIPT"] = str(script)
    env["OUTPUT_CSV"] = str(workdir / "report.csv")
    env["OUTPUT_TREE"] = str(workdir / "tree.txt")
    # Keep the placeholder path from being treated as a live credential.
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    completed = subprocess.run(
        [sys.executable, "-c", RUNNER],
        cwd=workdir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{script.name} exited {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


class TreeTabDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = scanner_entries(FILES)
        cls.files_json = json.dumps({"files": FILES})
        cls.drives_json = json.dumps({"drives": DRIVES})
        # The wire value is a JSON string escape, and the client turns it back
        # into a real tab. If this fails, the fixture never reaches print_tree.
        decoded = drive_json_response(json.loads(cls.files_json))
        names = [item["name"] for item in decoded["files"]]
        if "\tFinance" not in names or "\t\tArchive" not in names or "Q1\tActuals" not in names:
            raise AssertionError(f"JsonModel dropped tabs from folder names: {names!r}")

        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.org_dir = root / "org"
        cls.single_dir = root / "single"
        cls.org_dir.mkdir()
        cls.single_dir.mkdir()
        placeholder = {"type": "service_account", "client_email": "placeholder@example.com"}
        for directory in (cls.org_dir, cls.single_dir):
            (directory / "service-account-placeholder.json").write_text(
                json.dumps(placeholder), encoding="utf-8"
            )

        run_scanner(ORGWIDE_SCRIPT, ["shared_drive_orgwide_analysis.py"], cls.org_dir, cls.files_json, cls.drives_json)
        tree_files = list((cls.org_dir / "reports").glob("*_tree_Ops_Drive.txt"))
        if len(tree_files) != 1:
            raise AssertionError(f"expected one org-wide tree file, found {tree_files}")
        cls.org_text = tree_files[0].read_text(encoding="utf-8")
        cls.org_lines = cls.org_text.splitlines()

        cls.single = run_scanner(
            SINGLE_SCRIPT,
            ["shared_drive_analysis.py", "--drive-id", DRIVE_ID, "--verbose"],
            cls.single_dir,
            cls.files_json,
            cls.drives_json,
        )
        cls.single_text = (cls.single_dir / "tree.txt").read_text(encoding="utf-8")
        cls.single_lines = cls.single_text.splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_orgwide_tree_file_depth_follows_parent_links(self):
        assert_tree_matches_parents(self, self.org_lines, self.entries)
        by_name = {}
        for depth, name in self.entries:
            for line in self.org_lines:
                if line_matches_depth(line, depth, name):
                    by_name[(depth, name)] = line
        evil = by_name[(0, "\tFinance")]
        payroll = by_name[(1, "Payroll")]
        archive = by_name[(0, "\t\tArchive")]
        self.assertLess(first_content_column(evil), first_content_column(payroll))
        self.assertEqual(first_content_column(archive), 0)
        self.assertNotIn("\t", self.org_text)

    def test_single_drive_tree_file_depth_follows_parent_links(self):
        assert_tree_matches_parents(self, self.single_lines, self.entries)
        self.assertNotIn("\t", self.single_text)

    def test_single_drive_verbose_stdout_uses_the_same_lines(self):
        stdout_lines = self.single.stdout.splitlines()
        position = 0
        for line in self.single_lines:
            while position < len(stdout_lines) and stdout_lines[position] != line:
                position += 1
            self.assertLess(
                position,
                len(stdout_lines),
                f"verbose stdout did not print tree line {line!r}\n{self.single.stdout}",
            )
            position += 1
        # The printed lines are the file lines, so they must already be honest.
        assert_tree_matches_parents(self, self.single_lines, self.entries)

    def test_ordinary_names_keep_two_space_indent(self):
        # Holds before and after the tab fix. Guards a fix that drops real indents.
        self.assertIn("Projects/", self.org_lines)
        self.assertIn("  Finance/", self.org_lines)
        self.assertIn("    Notes/", self.org_lines)
        self.assertIn("  Team Files/", self.org_lines)


if __name__ == "__main__":
    unittest.main()
