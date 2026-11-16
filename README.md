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

## Org-wide scan

python shared_drive_orgwide_analysis.py

Scans every Shared Drive the service account is a member of and writes two files per drive into `reports/`:

- `YYYYMMDD_report_<label>_<drive id>.csv`
- `YYYYMMDD_tree_<label>_<drive id>.txt`

`<label>` is the drive name reduced to letters, digits, `_` and `-` (at most 60 characters). The drive id keeps files for drives with the same name apart. A drive whose id is not made of those characters is skipped with an error message.

## Tests

python3 -m unittest discover -s tests

The tests replace the Google API client with fakes and need no credentials or network access.

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->
