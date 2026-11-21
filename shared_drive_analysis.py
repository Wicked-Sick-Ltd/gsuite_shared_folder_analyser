import os
from dotenv import load_dotenv
load_dotenv()

# === CONFIGURATION ===
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
OUTPUT_CSV = os.getenv('OUTPUT_CSV', 'shared_drive_report.csv')
OUTPUT_TREE = os.getenv('OUTPUT_TREE', 'tree.txt')
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']

# ✅ Now it's safe to validate
if not SERVICE_ACCOUNT_FILE or not os.path.isfile(SERVICE_ACCOUNT_FILE):
    raise FileNotFoundError(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")

print("SERVICE_ACCOUNT_FILE loaded:", SERVICE_ACCOUNT_FILE)

# === DEPENDENCIES ===
import argparse
import csv
from collections import defaultdict
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from tqdm import tqdm


# === ARGUMENT PARSER ===
parser = argparse.ArgumentParser(description='Analyze Google Shared Drive')
parser.add_argument('--drive-id', required=True, help='Shared Drive ID')
parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
args = parser.parse_args()

# === AUTHENTICATION ===
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)


# === UTILS ===
def human_readable_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

def get_all_files(drive_id):
    files = []
    page_token = None
    if args.verbose:
        print("Getting files from shared drive...")
    while True:
        response = drive_service.files().list(
            corpora='drive',
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name, mimeType, parents, size, modifiedTime, owners)",
            pageToken=page_token
        ).execute()
        batch = response.get('files', [])
        files.extend(batch)
        if args.verbose:
            print(f"Fetched {len(batch)} files, total so far: {len(files)}")
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return files

def build_tree(files):
    file_map = {}
    children = defaultdict(list)
    for file in files:
        file_map[file['id']] = file
        for parent in file.get('parents', []):
            children[parent].append(file)
    return file_map, children

def get_size_and_details(file, children, file_map):
    if file['mimeType'] == 'application/vnd.google-apps.folder':
        total_size, total_count = 0, 0
        latest_modified = file.get('modifiedTime', '1970-01-01T00:00:00Z')
        for child in children.get(file['id'], []):
            size, count, last_mod = get_size_and_details(child, children, file_map)
            total_size += size
            total_count += count
            if last_mod > latest_modified:
                latest_modified = last_mod
        return total_size, total_count, latest_modified
    else:
        size = int(file.get('size', 0))
        return size, 1, file.get('modifiedTime', '1970-01-01T00:00:00Z')

def is_folder(file):
    return file['mimeType'] == 'application/vnd.google-apps.folder'

def get_unfiled_files(files, file_map):
    # Files at the shared-drive root have the drive id as their parent, which is
    # never in file_map, so no folder rollup reaches them.
    return [
        f for f in files
        if not is_folder(f)
        and not any(is_folder(file_map[p]) for p in f.get('parents', []) if p in file_map)
    ]

def get_drive_totals(files):
    non_folders = [f for f in files if not is_folder(f)]
    size = sum(int(f.get('size', 0)) for f in non_folders)
    latest_modified = max(
        (f.get('modifiedTime', '1970-01-01T00:00:00Z') for f in non_folders),
        default='1970-01-01T00:00:00Z'
    )
    return size, len(non_folders), latest_modified

def get_folder_path(file_id, file_map):
    path = []
    while file_id in file_map:
        file = file_map[file_id]
        path.append(file['name'])
        parents = file.get('parents')
        if not parents:
            break
        file_id = parents[0]
    return '/'.join(reversed(path))

def print_tree(file, children, file_map, prefix='', out_lines=None):
    line = f"{prefix}{file['name']}/"
    if out_lines is not None:
        out_lines.append(line)
    if args.verbose:
        print(line)
    for child in sorted(children.get(file['id'], []), key=lambda x: x['name']):
        if child['mimeType'] == 'application/vnd.google-apps.folder':
            print_tree(child, children, file_map, prefix + '  ', out_lines)

# === MAIN ===
files = get_all_files(args.drive_id)
file_map, children = build_tree(files)
folders = [f for f in files if is_folder(f)]
unfiled_files = get_unfiled_files(files, file_map)

if args.verbose:
    print(f"Processing {len(folders)} folders and {len(unfiled_files)} root-level files...")

# Write CSV
with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow([
        'Folder Path', 'File Count', 'Total Size (Bytes)', 'Readable Size',
        'Latest Modified Time', 'Owner Email', 'MimeType', 'ID'
    ])

    size, count, last_modified = get_drive_totals(files)
    writer.writerow([
        '/', count, size, human_readable_size(size),
        last_modified, 'unknown', 'shared_drive', args.drive_id
    ])

    for folder in tqdm(folders, desc="Analyzing folders"):
        size, count, last_modified = get_size_and_details(folder, children, file_map)
        path = get_folder_path(folder['id'], file_map)
        owner = folder.get('owners', [{}])[0].get('emailAddress', 'unknown')
        writer.writerow([
            path, count, size, human_readable_size(size),
            last_modified, owner, 'folder', folder['id']
        ])

    for file in sorted(unfiled_files, key=lambda x: x['name']):
        size, count, last_modified = get_size_and_details(file, children, file_map)
        owner = file.get('owners', [{}])[0].get('emailAddress', 'unknown')
        writer.writerow([
            file['name'], count, size, human_readable_size(size),
            last_modified, owner, file['mimeType'], file['id']
        ])

# Write Tree
tree_lines = []
for folder in sorted(folders, key=lambda x: x['name']):
    if not any(parent in file_map for parent in folder.get('parents', [])):
        print_tree(folder, children, file_map, '', tree_lines)
for file in sorted(unfiled_files, key=lambda x: x['name']):
    tree_lines.append(file['name'])
    if args.verbose:
        print(file['name'])

with open(OUTPUT_TREE, 'w', encoding='utf-8') as f:
    f.write('\n'.join(tree_lines))

print(f"✅ CSV saved to {OUTPUT_CSV}")
print(f"✅ Tree saved to {OUTPUT_TREE}")

