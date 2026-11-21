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

`shared_drive_orgwide_analysis.py` writes the same outputs for every Shared Drive the service account can see, under `reports/`.

Each CSV row keeps the original columns in their previous positions and adds `Folder ID` and `Parent ID` at the end (the Drive ID for top-level folders). Use those ids, not `Folder Path`, as the identity of a folder. In `Folder Path` and the tree, a `/` or `\` that is part of a folder name is written as `\/` or `\\`, so an unescaped `/` always separates a parent from its child.

## Tests

python3 -m unittest discover -s tests

The tests stub the Google client libraries and need no credentials or network access.

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->
