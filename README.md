# Shared Drive Analyzer

A Python tool to crawl a Google Shared Drive, output a folder tree with sizes, file counts, and export a CSV and tree view.

## Setup

1. Create a service account in GCP with Drive API access.
2. Share the Shared Drive with that service account.
3. Install dependencies:


pip install -r requirements.txt


## Cloud agent environment

Cursor Cloud Agents run `bash .cursor/install.sh` (see `.cursor/environment.json`). The script creates `.venv`, installs `requirements.txt`, and is safe to run again. It does not call Google.

The crawler reads `SERVICE_ACCOUNT_FILE`, a path to a Google service account JSON file. If that variable is unset or the file is missing, install prints a warning and still succeeds. Do not commit the JSON, `.env`, or any other credential.

Tests are `python3 -m unittest discover -s tests`. There is no linter config. Both entry scripts import `python-dotenv`, which is not listed in `requirements.txt`, so a clean install cannot import them until that package is added.

## Usage

python shared_drive_analysis.py --drive-id [DRIVE_ID] --verbose

## Outputs

- `shared_drive_report.csv`
- `tree.txt`

`shared_drive_orgwide_analysis.py` writes the same CSV and tree for every Shared Drive the service account can list, under `reports/`.

Each CSV starts with a drive-root row (`Folder Path` `/`, `MimeType` `drive`) totalling every file in the drive, followed by one row per folder, then one row per file stored directly at the drive root (with its own MimeType). The tree lists folders only.

Each row keeps the original columns in their previous positions and adds `Folder ID` and `Parent ID` at the end. For a folder, `Parent ID` is the Drive ID when the folder is at the top level. For the drive-root row, `Folder ID` is the Drive ID and `Parent ID` is empty. A root-level file uses its own file id and parent id in those columns. Use the ids, not `Folder Path`, as the identity of a row. In `Folder Path` and the tree, a `/` or `\` that is part of a name is written as `\/` or `\\`, so an unescaped `/` always separates a parent from its child.

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
