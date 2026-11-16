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

The CSV has one `/` row (MimeType `shared_drive`) with the file count and size of every file in the drive. After that come one row per folder and one row per file that sits outside any listed folder, such as files at the shared-drive root, each with its own name, owner, size and MimeType. The `ID` column holds the Drive id of each row's item. `tree.txt` lists top-level folders followed by those root-level files.

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
