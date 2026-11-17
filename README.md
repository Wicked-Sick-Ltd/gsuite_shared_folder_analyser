# Shared Drive Analyzer

A Python tool to crawl a Google Shared Drive, output a folder tree with sizes, file counts, and export a CSV and tree view.

## Setup

1. Create a service account in GCP with Drive API access.
2. Share the Shared Drive with that service account.
3. Install dependencies:


pip install -r requirements.txt


## Usage

python shared_drive_analysis.py --drive-id [DRIVE_ID] --verbose

## Outputs

- `shared_drive_report.csv`
- `tree.txt`

Folder names are percent-encoded in both outputs so each name stays one path
segment and one tree line: `%`, `/`, `\`, line breaks and other non-printable
characters, and leading spaces are escaped; a name made only of dots and spaces
(such as `.` or `..`) is escaped entirely, and an empty name is written as
`%00`. Decode each `/`-separated segment with `urllib.parse.unquote` to get the
Drive name back, except the segment `%00`, which is an empty name.
`unquote('%00')` is a NUL, and a folder whose name is a single NUL encodes to
the same `%00`.

## Tests

python3 -m unittest discover -s tests

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->
