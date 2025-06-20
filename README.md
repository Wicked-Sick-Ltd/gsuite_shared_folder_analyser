# Shared Drive Analyzer

A Python tool to crawl a Google Shared Drive, output a folder tree with sizes, file counts, and export a CSV and tree view.

## Setup

1. Create a service account in GCP with Drive API access.
2. Share the Shared Drive with that service account.
3. Install dependencies:


pip install -r requirements.txt


## Usage

python shared_drive_analsis.py --drive-id [DRIVE_ID] --verbose

## Outputs

- `shared_drive_report.csv`
- `tree.txt`

 
