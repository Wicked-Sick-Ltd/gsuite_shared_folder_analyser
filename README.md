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

There is no test suite and no linter config in this repository. Both entry scripts import `python-dotenv`, which is not listed in `requirements.txt`, so a clean install cannot import them until that package is added.

## Usage

python shared_drive_analysis.py --drive-id [DRIVE_ID] --verbose

## Outputs

- `shared_drive_report.csv`
- `tree.txt`

<!-- repository-guidance:begin -->
## Contributing and agent guidance

- [Contributor guide](CONTRIBUTING.md): development workflow and validation.
- [Agent instructions](AGENTS.md): shared guidance for Codex and other coding agents.
- [Security policy](SECURITY.md): private vulnerability reporting.

## Repository license

MIT licensed; see [LICENSE](LICENSE). Preserve third-party notices.
<!-- repository-guidance:end -->
