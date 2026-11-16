from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv
from pathlib import Path
from collections import defaultdict
from tqdm import tqdm
import os
import csv
from datetime import datetime

# Load environment variables
load_dotenv()
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']
OUTPUT_DIR = "reports"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Validate service account file
if not SERVICE_ACCOUNT_FILE or not os.path.isfile(SERVICE_ACCOUNT_FILE):
    raise FileNotFoundError(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")

# Authenticate
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)

# Utility functions
def human_readable_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

def get_all_shared_drives():
    shared_drives = []
    page_token = None
    while True:
        response = drive_service.drives().list(
            pageSize=100,
            pageToken=page_token
        ).execute()
        shared_drives.extend(response.get('drives', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return shared_drives

def get_all_files(drive_id):
    files = []
    page_token = None
    while True:
        response = drive_service.files().list(
            corpora='drive',
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name, mimeType, parents, size, modifiedTime, owners)",
            pageToken=page_token
        ).execute()
        files.extend(response.get('files', []))
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
    for child in sorted(children.get(file['id'], []), key=lambda x: x['name']):
        if child['mimeType'] == 'application/vnd.google-apps.folder':
            print_tree(child, children, file_map, prefix + '  ', out_lines)

# Main execution
today = datetime.today().strftime('%Y%m%d')
shared_drives = get_all_shared_drives()

for drive in shared_drives:
    drive_name = drive['name'].replace(" ", "_")
    drive_id = drive['id']
    print(f"🔍 Processing Shared Drive: {drive_name} ({drive_id})")

    try:
        files = get_all_files(drive_id)
        file_map, children = build_tree(files)
        folders = [f for f in files if is_folder(f)]
        unfiled_files = get_unfiled_files(files, file_map)

        csv_path = os.path.join(OUTPUT_DIR, f"{today}_report_{drive_name}.csv")
        tree_path = os.path.join(OUTPUT_DIR, f"{today}_tree_{drive_name}.txt")

        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Folder Path', 'File Count', 'Total Size (Bytes)', 'Readable Size',
                'Latest Modified Time', 'Owner Email', 'MimeType', 'ID'
            ])

            size, count, last_modified = get_drive_totals(files)
            writer.writerow([
                '/', count, size, human_readable_size(size),
                last_modified, 'unknown', 'shared_drive', drive_id
            ])

            for folder in tqdm(folders, desc=f"Analyzing {drive_name}"):
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

        tree_lines = []
        for folder in sorted(folders, key=lambda x: x['name']):
            if not any(parent in file_map for parent in folder.get('parents', [])):
                print_tree(folder, children, file_map, '', tree_lines)
        tree_lines.extend(f['name'] for f in sorted(unfiled_files, key=lambda x: x['name']))

        with open(tree_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(tree_lines))

        print(f"✅ Done: {drive_name} → {csv_path}, {tree_path}")

    except Exception as e:
        print(f"❌ Error processing {drive_name}: {e}")

