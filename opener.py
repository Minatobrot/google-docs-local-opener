import sys
import os
import subprocess
import traceback
import json
import hashlib

# ==============================================================================
# USER CONFIGURATION
# ==============================================================================
# Preferred browser: "firefox", "chrome", "edge", or "default" (system default)
PREFERRED_BROWSER = "firefox"

# Set to True to delete the local Office file after uploading to Google Drive
DELETE_LOCAL_AFTER_UPLOAD = False
# ==============================================================================

FOLDER_PATH = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(FOLDER_PATH, "python_log.txt")
CACHE_FILE = os.path.join(FOLDER_PATH, "upload_cache.json")
TOKEN_FILE = os.path.join(FOLDER_PATH, "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

MIME_MAP = {
    ".doc": (
        "application/vnd.google-apps.document",
        "application/msword",
    ),
    ".docx": (
        "application/vnd.google-apps.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".xls": (
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.ms-excel",
    ),
    ".xlsx": (
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".ppt": (
        "application/vnd.google-apps.presentation",
        "application/vnd.ms-powerpoint",
    ),
    ".pptx": (
        "application/vnd.google-apps.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".odt": (
        "application/vnd.google-apps.document",
        "application/vnd.oasis.opendocument.text",
    ),
    ".ods": (
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.oasis.opendocument.spreadsheet",
    ),
    ".odp": (
        "application/vnd.google-apps.presentation",
        "application/vnd.oasis.opendocument.presentation",
    ),
}


def log(msg):
    print(msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def reset_log():
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("--- GOOGLE DOCS OPENER LOG ---\n")
    except Exception:
        pass


def get_file_sha256(file_path):
    """Calculates a unique SHA-256 fingerprint of the file's contents.
    Moving or renaming the file will not change this fingerprint."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4, ensure_ascii=False)


def open_url(url):
    """Opens the URL in the preferred browser, falling back to system default."""
    browser_paths = {
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
    }

    pref = PREFERRED_BROWSER.lower()
    if pref in browser_paths:
        exe = next((p for p in browser_paths[pref] if os.path.exists(p)), None)
        if exe:
            subprocess.Popen([exe, url])
            return

    os.startfile(url)


def get_drive_service():
    try:
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        log("ERROR: Missing Google API libraries. Please run setup.bat first!")
        sys.exit(1)

    creds_file = os.path.join(FOLDER_PATH, "credentials.json")
    alt_creds = os.path.join(FOLDER_PATH, "credentials.json.json")
    if not os.path.exists(creds_file) and os.path.exists(alt_creds):
        creds_file = alt_creds

    if not os.path.exists(creds_file):
        log(f"ERROR: 'credentials.json' not found inside {FOLDER_PATH}")
        log("Please follow the README instructions to add your credentials.json file.")
        sys.exit(1)

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            log("Opening browser for one-time Google Account login...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def cloud_file_still_exists(service, file_id):
    """Checks if the previously uploaded file still exists and is not in Google Drive's trash."""
    if not file_id:
        return False
    try:
        meta = service.files().get(fileId=file_id, fields="id, trashed").execute()
        return not meta.get("trashed", False)
    except Exception:
        return False


def main():
    reset_log()

    if len(sys.argv) < 2 or not sys.argv[1].strip():
        log("ERROR: No file was passed to the script.")
        log("Drag and drop a .docx, .xlsx, or .pptx file onto run_docs.bat, or double-click an associated file.")
        sys.exit(1)

    file_path = os.path.abspath(sys.argv[1])
    log(f"Selected file: {file_path}")

    if not os.path.exists(file_path):
        log(f"ERROR: File does not exist at path: {file_path}")
        sys.exit(1)

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in MIME_MAP:
        log(f"ERROR: Unsupported file extension '{ext}'. Supported: {', '.join(MIME_MAP.keys())}")
        sys.exit(1)

    # 1. Calculate SHA-256 content hash so moving or renaming the file still matches!
    file_hash = get_file_sha256(file_path)
    log(f"File SHA-256 Fingerprint: {file_hash}")

    cache = load_cache()
    service = get_drive_service()

    # 2. Check if this exact file content was already uploaded
    if file_hash in cache:
        entry = cache[file_hash]
        # Support both old string format and new dict format
        if isinstance(entry, dict):
            file_id = entry.get("id")
            cached_url = entry.get("url")
        else:
            file_id = None
            cached_url = entry

        if file_id and cloud_file_still_exists(service, file_id):
            log("Match found in cache (even if moved/renamed)! Opening existing Google Doc...")
            open_url(cached_url)
            return
        elif not file_id and cached_url:
            log("Match found in legacy cache! Opening existing Google Doc...")
            open_url(cached_url)
            return
        else:
            log("Cached file was deleted from Google Drive. Re-uploading a fresh copy...")

    # 3. Upload & convert to Google Docs / Sheets / Slides
    from googleapiclient.http import MediaFileUpload

    g_mime, source_mime = MIME_MAP[ext]
    log(f"Uploading '{os.path.basename(file_path)}' to Google Drive...")

    file_metadata = {
        "name": os.path.basename(file_path),
        "mimeType": g_mime,
    }
    media = MediaFileUpload(file_path, mimetype=source_mime, resumable=True)
    uploaded = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )

    file_id = uploaded.get("id")
    doc_url = uploaded.get("webViewLink") or f"https://drive.google.com/open?id={file_id}"

    # 4. Save by SHA-256 hash so moving/renaming the file never re-uploads it
    cache[file_hash] = {
        "id": file_id,
        "url": doc_url,
        "last_known_name": os.path.basename(file_path),
    }
    save_cache(cache)

    log(f"Success! Opening: {doc_url}")
    open_url(doc_url)

    if DELETE_LOCAL_AFTER_UPLOAD:
        try:
            os.remove(file_path)
            log("Deleted local file after successful upload.")
        except Exception as e:
            log(f"Could not delete local file: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("\n--- UNEXPECTED CRASH REPORT ---")
        log(traceback.format_exc())
        sys.exit(1)
