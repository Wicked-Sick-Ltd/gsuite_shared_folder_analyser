from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv
from pathlib import Path
from collections import defaultdict
from tqdm import tqdm
import os
import csv
import unicodedata
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

# Bidi controls can spoof a label on screen. They are format characters, not Cc.
_TERMINAL_BIDI = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_TERMINAL_LINE_SEPARATORS = frozenset("\u2028\u2029")

def terminal_text(text):
    # Drive names are set by drive organizers. Drop C0/C1 controls (newline,
    # carriage return, ESC, DEL), bidi controls, and line/paragraph separators
    # so a name cannot forge log lines or spoof the terminal. Other spaces
    # become a normal space so word boundaries stay visible.
    kept = []
    for ch in str(text):
        if (
            unicodedata.category(ch) == "Cc"
            or ch in _TERMINAL_BIDI
            or ch in _TERMINAL_LINE_SEPARATORS
        ):
            continue
        if unicodedata.category(ch) == "Zs" and ch != " ":
            kept.append(" ")
        else:
            kept.append(ch)
    return "".join(kept)

def report_file_token(drive_id):
    # The id is part of the report path. Keep it from adding separators.
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(drive_id))
    return safe or "unknown"

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

# Main execution
today = datetime.today().strftime('%Y%m%d')
shared_drives = get_all_shared_drives()

for drive in shared_drives:
    drive_id = drive['id']
    # Console label only. Report paths also include the drive id so two names
    # that differ by stripped characters cannot overwrite each other's files.
    drive_name = terminal_text(drive['name']).replace(" ", "_")
    file_token = report_file_token(drive_id)
    report_name = f"{drive_name}_{file_token}" if drive_name else file_token
    print(f"🔍 Processing Shared Drive: {drive_name} ({drive_id})")

    try:
        files = get_all_files(drive_id)
        file_map, children = build_tree(files)
        folders = [f for f in files if f['mimeType'] == 'application/vnd.google-apps.folder']

        csv_path = os.path.join(OUTPUT_DIR, f"{today}_report_{report_name}.csv")
        tree_path = os.path.join(OUTPUT_DIR, f"{today}_tree_{report_name}.txt")

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

        print(f"✅ Done: {drive_name} ({drive_id}) → {csv_path}, {tree_path}")

    except Exception as e:
        print(f"❌ Error processing {drive_name} ({drive_id}): {terminal_text(e)}")

