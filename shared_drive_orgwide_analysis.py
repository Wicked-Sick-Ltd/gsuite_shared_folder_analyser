from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv
from pathlib import Path
from collections import defaultdict
from tqdm import tqdm
import os
import csv
import re
from datetime import datetime

# Load environment variables
load_dotenv()
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']
OUTPUT_DIR = "reports"
os.makedirs(OUTPUT_DIR, exist_ok=True)
DRIVE_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]+')
UNSAFE_LABEL_CHARS = re.compile(r'[^A-Za-z0-9_-]+')
MAX_LABEL_LENGTH = 60

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

def report_path(date_prefix, kind, drive_id, drive_name, extension):
    # Drive names are chosen by whoever manages the drive, so only a
    # restricted label derived from them may reach the filename.
    if not DRIVE_ID_PATTERN.fullmatch(drive_id):
        raise ValueError(f"Unexpected shared drive id: {drive_id!r}")
    label = UNSAFE_LABEL_CHARS.sub('_', drive_name).strip('_')[:MAX_LABEL_LENGTH]
    stem = f"{date_prefix}_{kind}_{label}_{drive_id}" if label else f"{date_prefix}_{kind}_{drive_id}"
    path = os.path.join(OUTPUT_DIR, f"{stem}.{extension}")
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(OUTPUT_DIR):
        raise ValueError(f"Report path escapes {OUTPUT_DIR}: {path!r}")
    return path

# Main execution
today = datetime.today().strftime('%Y%m%d')
shared_drives = get_all_shared_drives()

for drive in shared_drives:
    drive_name = drive['name']
    drive_id = drive['id']
    print(f"🔍 Processing Shared Drive: {drive_name} ({drive_id})")

    try:
        files = get_all_files(drive_id)
        file_map, children = build_tree(files)
        folders = [f for f in files if f['mimeType'] == 'application/vnd.google-apps.folder']

        csv_path = report_path(today, 'report', drive_id, drive_name, 'csv')
        tree_path = report_path(today, 'tree', drive_id, drive_name, 'txt')

        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Folder Path', 'File Count', 'Total Size (Bytes)', 'Readable Size',
                'Latest Modified Time', 'Owner Email', 'MimeType'
            ])

            for folder in tqdm(folders, desc=f"Analyzing {drive_name}"):
                size, count, last_modified = get_size_and_details(folder, children, file_map)
                path = get_folder_path(folder['id'], file_map)
                owner = folder.get('owners', [{}])[0].get('emailAddress', 'unknown')
                writer.writerow([
                    path, count, size, human_readable_size(size),
                    last_modified, owner, 'folder'
                ])

        tree_lines = []
        for folder in sorted(folders, key=lambda x: x['name']):
            if not any(parent in file_map for parent in folder.get('parents', [])):
                print_tree(folder, children, file_map, '', tree_lines)

        with open(tree_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(tree_lines))

        print(f"✅ Done: {drive_name} → {csv_path}, {tree_path}")

    except Exception as e:
        print(f"❌ Error processing {drive_name}: {e}")

