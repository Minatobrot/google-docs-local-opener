import sys
import os
import shutil
import glob
import subprocess
import traceback
import json
import hashlib
import time
import threading
import types
import ctypes
from ctypes import wintypes
import winreg


# ==============================================================================
# NATIVE DLL PRELOADER & CRYPTOGRAPHY / _RUST FALLBACK GUARD
# Must execute BEFORE importing googleapiclient / google.auth / cryptography!
# ==============================================================================
def bootstrap_runtime_environment():
    """
    1. Registers PyInstaller _MEIPASS and cryptography bindings dir in Windows DLL search path.
    2. Preloads vcruntime140*.dll and python3.dll into process memory via ctypes.WinDLL so
       cryptography's abi3 _rust.pyd extension resolves python3.dll immediately.
    3. If _rust.pyd still fails to load on a machine, stubs google.auth.crypt.es, es256,
       and _cryptography_rsa so google-auth falls back cleanly without crashing.
    """
    dll_dirs = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        dll_dirs.append(sys._MEIPASS)
        dll_dirs.append(os.path.join(sys._MEIPASS, "cryptography", "hazmat", "bindings"))
    dll_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))

    path_env = os.environ.get("PATH", "")
    for d in dll_dirs:
        if d and os.path.isdir(d):
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
            if d not in path_env:
                path_env = d + os.pathsep + path_env
    os.environ["PATH"] = path_env

    # Preload critical runtime DLLs into process memory so _rust.pyd finds them immediately
    for d in dll_dirs:
        if not d or not os.path.isdir(d):
            continue
        for dll_name in ("vcruntime140.dll", "vcruntime140_1.dll", "python3.dll"):
            candidate = os.path.join(d, dll_name)
            if os.path.exists(candidate):
                try:
                    ctypes.WinDLL(candidate)
                except Exception:
                    pass

    # Verify _rust loads; if not, activate pure-Python google.auth.crypt fallback
    try:
        from cryptography.hazmat.bindings import _rust  # noqa: F401
    except Exception:
        for mod_name in list(sys.modules.keys()):
            if mod_name == "cryptography" or mod_name.startswith("cryptography."):
                sys.modules.pop(mod_name, None)

        class _UnavailableSignerVerifier:
            def __init__(self, *args, **kwargs):
                raise NotImplementedError("Service account signing is not used in OAuth Desktop flow.")

            @classmethod
            def from_string(cls, *args, **kwargs):
                raise NotImplementedError("Service account signing is not used in OAuth Desktop flow.")

        es_stub = types.ModuleType("google.auth.crypt.es")
        es_stub.EsSigner = _UnavailableSignerVerifier
        es_stub.EsVerifier = _UnavailableSignerVerifier
        sys.modules["google.auth.crypt.es"] = es_stub

        es256_stub = types.ModuleType("google.auth.crypt.es256")
        es256_stub.ES256Signer = _UnavailableSignerVerifier
        es256_stub.ES256Verifier = _UnavailableSignerVerifier
        sys.modules["google.auth.crypt.es256"] = es256_stub

        rsa_stub = types.ModuleType("google.auth.crypt.rsa")
        try:
            from google.auth.crypt import _python_rsa
            rsa_stub.RSASigner = _python_rsa.RSASigner
            rsa_stub.RSAVerifier = _python_rsa.RSAVerifier
        except Exception:
            rsa_stub.RSASigner = _UnavailableSignerVerifier
            rsa_stub.RSAVerifier = _UnavailableSignerVerifier
        sys.modules["google.auth.crypt.rsa"] = rsa_stub


# ==============================================================================
# 64-BIT SAFE PYINSTALLER BUILDER (Invoked by `build.bat` via `opener.py --build`)
# ==============================================================================
def get_loaded_python_dll_dir():
    """Safely retrieves the directory of the active python3XX.dll on 64-bit & 32-bit Windows."""
    try:
        handle = getattr(sys, "dllhandle", None)
        if not handle:
            return ""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        kernel32.GetModuleFileNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        res = kernel32.GetModuleFileNameW(wintypes.HMODULE(handle), buf, 1024)
        if res > 0 and buf.value:
            return os.path.dirname(os.path.abspath(buf.value))
    except Exception:
        pass
    return ""


def run_pyinstaller_build():
    """Builds GoogleDocsOpener.exe with all required native DLLs and icon.ico."""
    import PyInstaller.__main__

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    raw_dirs = [
        get_loaded_python_dll_dir(),
        sys.base_prefix,
        sys.prefix,
        getattr(sys, "base_exec_prefix", ""),
        getattr(sys, "exec_prefix", ""),
        os.path.dirname(os.path.abspath(sys.executable)),
        os.path.join(sys.base_prefix, "DLLs"),
        os.path.join(sys.base_prefix, "Library", "bin"),
    ]
    search_dirs = []
    for d in raw_dirs:
        if d and os.path.isdir(d) and os.path.normcase(d) not in [os.path.normcase(x) for x in search_dirs]:
            search_dirs.append(d)

    args = [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--distpath",
        ".",
        "--name",
        "GoogleDocsOpener",
        "--collect-all",
        "googleapiclient",
        "--collect-all",
        "google_auth_oauthlib",
        "--collect-all",
        "cryptography",
        "--collect-all",
        "cffi",
        "--hidden-import",
        "google.auth.crypt._python_rsa",
        "--hidden-import",
        "rsa",
    ]

    dll_patterns = ("python3.dll", "vcruntime140.dll", "vcruntime140_1.dll", "libcrypto*.dll", "libssl*.dll")
    added_dlls = set()

    for d in search_dirs:
        for pat in dll_patterns:
            for match in glob.glob(os.path.join(d, pat)):
                if os.path.isfile(match):
                    dll_name = os.path.basename(match).lower()
                    if dll_name not in added_dlls:
                        args.extend([
                            "--add-binary", f"{match};.",
                            "--add-binary", f"{match};cryptography/hazmat/bindings",
                        ])
                        added_dlls.add(dll_name)
                        print(f"[DLL] Bundling {dll_name} from: {match}")

    icon_file = os.path.join(script_dir, "icon.ico")
    if os.path.exists(icon_file):
        print(f"[ICON] Embedding custom icon: {icon_file}")
        args.extend([f"--icon={icon_file}", "--add-data", f"{icon_file};."])
    else:
        print("[WARN] icon.ico not found in script folder; using default icon.")

    args.append("opener.py")
    print("[BUILD] Running PyInstaller...")
    PyInstaller.__main__.run(args)


if len(sys.argv) >= 2 and sys.argv[1] == "--build":
    run_pyinstaller_build()
    sys.exit(0)

bootstrap_runtime_environment()

# Top-level Google API imports (protected by bootstrap_runtime_environment)
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from google.auth.transport.requests import Request  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402

# High-DPI scaling and Windows 10/11 Taskbar icon grouping
APP_VERSION = "2.2"
APP_USER_MODEL_ID = "GoogleDocsOpener.Desktop.App.v2"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
except Exception:
    pass

# ==============================================================================
# SAFE APPDATA STORAGE PATHS
# ==============================================================================
LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
APP_DATA_DIR = os.path.join(LOCAL_APPDATA, "GoogleDocsOpener")
os.makedirs(APP_DATA_DIR, exist_ok=True)

INSTALLED_EXE_PATH = os.path.join(APP_DATA_DIR, "GoogleDocsOpener.exe")
INSTALLED_ICON_PATH = os.path.join(APP_DATA_DIR, "icon.ico")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")
LOG_FILE = os.path.join(APP_DATA_DIR, "python_log.txt")
CACHE_FILE = os.path.join(APP_DATA_DIR, "upload_cache.json")
TOKEN_FILE = os.path.join(APP_DATA_DIR, "token.json")
CREDS_FILE = os.path.join(APP_DATA_DIR, "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

DEFAULT_CONFIG = {
    "preferred_browser": "firefox",
    "delete_local_after_upload": False,
    "use_drive_folder": True,
    "drive_folder_name": "Google Docs Opener Uploads",
    "drive_folder_id": "",
    "watch_folder": "",
    "autostart_watcher": False,
}

MIME_MAP = {
    ".doc": ("application/vnd.google-apps.document", "application/msword", "Document"),
    ".docx": ("application/vnd.google-apps.document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Document"),
    ".odt": ("application/vnd.google-apps.document", "application/vnd.oasis.opendocument.text", "Document"),
    ".rtf": ("application/vnd.google-apps.document", "application/rtf", "Document"),
    ".xls": ("application/vnd.google-apps.spreadsheet", "application/vnd.ms-excel", "Spreadsheet"),
    ".xlsx": ("application/vnd.google-apps.spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Spreadsheet"),
    ".ods": ("application/vnd.google-apps.spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", "Spreadsheet"),
    ".csv": ("application/vnd.google-apps.spreadsheet", "text/csv", "Spreadsheet"),
    ".tsv": ("application/vnd.google-apps.spreadsheet", "text/tab-separated-values", "Spreadsheet"),
    ".ppt": ("application/vnd.google-apps.presentation", "application/vnd.ms-powerpoint", "Presentation"),
    ".pptx": ("application/vnd.google-apps.presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "Presentation"),
    ".odp": ("application/vnd.google-apps.presentation", "application/vnd.oasis.opendocument.presentation", "Presentation"),
}


# ==============================================================================
# ICON, LOGGING & CONFIGURATION HELPERS
# ==============================================================================
def get_icon_path():
    """Finds icon.ico whether running from source, bundled PyInstaller _MEIPASS, or AppData."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "icon.ico"))
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "icon.ico"))
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico"))
    candidates.append(INSTALLED_ICON_PATH)

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def apply_window_icon(win):
    """Applies icon.ico to any Tkinter Tk or Toplevel window."""
    icon_path = get_icon_path()
    try:
        if icon_path:
            win.iconbitmap(default=icon_path)
            return
        if getattr(sys, "frozen", False):
            win.iconbitmap(default=sys.executable)
    except Exception:
        pass


def show_message(title, text, style=0x40):
    """Native Windows MessageBox (0x10 = Error, 0x30 = Warning, 0x40 = Info, 0x04 = Yes/No)."""
    return ctypes.windll.user32.MessageBoxW(0, str(text), str(title), style)


def init_log():
    """Initializes log file without erasing recent crash diagnostics; trims if > 512 KB."""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-400:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n--- SESSION START ({time.strftime('%Y-%m-%d %H:%M:%S')}) | Args: {sys.argv[1:]} ---\n")
    except Exception:
        pass


def log(msg):
    print(msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def clear_log():
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"--- LOG CLEARED ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
    except Exception:
        pass


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        log(f"Error saving config: {e}")


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Error saving cache: {e}")


def get_file_sha256(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def open_url(url):
    cfg = load_config()
    pref = cfg.get("preferred_browser", "firefox").lower()

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
        "brave": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ],
        "vivaldi": [
            os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe"),
            r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
        ],
    }

    if pref in browser_paths:
        exe = next((p for p in browser_paths[pref] if os.path.exists(p)), None)
        if exe:
            subprocess.Popen([exe, url])
            return

    os.startfile(url)


# ==============================================================================
# CREDENTIALS AUTO-SCANNER & VALIDATOR
# ==============================================================================
def validate_and_import_credentials(source_path):
    """Verifies that the selected JSON file is a valid Google OAuth Desktop Client file."""
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False, "The selected file is not a valid JSON file."

    if "web" in data and "installed" not in data:
        return (
            False,
            "MISTAKE DETECTED: You selected 'Web application' instead of 'Desktop app' "
            "when creating your OAuth Client ID in Google Cloud!\n\n"
            "Fix: Go back to Google Cloud Console -> Clients -> + Create Client -> "
            "choose 'Desktop app' -> Download JSON.",
        )

    if "type" in data and data.get("type") == "service_account":
        return (
            False,
            "MISTAKE DETECTED: You downloaded a 'Service Account' key instead of an 'OAuth client ID'!\n\n"
            "Fix: Click '+ Create Credentials' -> 'OAuth client ID' -> 'Desktop app'.",
        )

    if "installed" not in data:
        return (
            False,
            "This JSON file is not a Google OAuth Desktop credential file.\n"
            "Make sure you downloaded the JSON from 'OAuth 2.0 Client IDs' (Desktop app).",
        )

    if os.path.normcase(os.path.abspath(source_path)) != os.path.normcase(os.path.abspath(CREDS_FILE)):
        shutil.copyfile(source_path, CREDS_FILE)
    log(f"Imported valid credentials.json from: {source_path}")
    return True, "Successfully imported and verified credentials.json!"


def auto_scan_for_credentials():
    """Scans the app folder and Downloads folder for Google OAuth JSON files."""
    if os.path.exists(CREDS_FILE):
        return True, "Ready"

    search_dirs = []
    if getattr(sys, "frozen", False):
        search_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))

    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.exists(downloads_dir):
        search_dirs.append(downloads_dir)

    patterns = ["credentials.json", "credentials.json.json", "client_secret*.json"]
    for d in search_dirs:
        for pat in patterns:
            matches = sorted(glob.glob(os.path.join(d, pat)), key=os.path.getmtime, reverse=True)
            for match in matches:
                ok, _ = validate_and_import_credentials(match)
                if ok:
                    return True, f"Auto-detected and imported from:\n{match}"
    return False, "Not found"


# ==============================================================================
# SELF-INSTALLATION, ICON PERSISTENCE, FILE ASSOCIATIONS & AUTOSTART
# ==============================================================================
def sync_icon_to_appdata():
    """Copies icon.ico to %LOCALAPPDATA%\\GoogleDocsOpener\\icon.ico so shortcuts & registry always have it."""
    src_icon = get_icon_path()
    if src_icon and os.path.exists(src_icon):
        if os.path.normcase(os.path.abspath(src_icon)) != os.path.normcase(os.path.abspath(INSTALLED_ICON_PATH)):
            try:
                shutil.copy2(src_icon, INSTALLED_ICON_PATH)
            except Exception as e:
                log(f"Note copying icon.ico to AppData: {e}")


def install_to_safe_location():
    """Copies the executable and icon.ico into %LOCALAPPDATA%\\GoogleDocsOpener and creates Start Menu shortcut."""
    sync_icon_to_appdata()

    if not getattr(sys, "frozen", False):
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    current_exe = os.path.abspath(sys.executable)
    if os.path.normcase(current_exe) != os.path.normcase(INSTALLED_EXE_PATH):
        try:
            shutil.copy2(current_exe, INSTALLED_EXE_PATH)
            log(f"Self-installed permanent copy to: {INSTALLED_EXE_PATH}")
        except Exception as e:
            log(f"Could not copy exe to AppData: {e}")

    try:
        start_menu = os.path.join(
            os.environ.get("APPDATA", ""),
            r"Microsoft\Windows\Start Menu\Programs\Google Docs Opener.lnk",
        )
        icon_loc = INSTALLED_ICON_PATH if os.path.exists(INSTALLED_ICON_PATH) else f"{INSTALLED_EXE_PATH},0"
        ps_cmd = (
            f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{start_menu}");'
            f'$s.TargetPath="{INSTALLED_EXE_PATH}";'
            f'$s.WorkingDirectory="{APP_DATA_DIR}";'
            f'$s.IconLocation="{icon_loc}";'
            f'$s.Description="Google Docs Opener Control Panel";'
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            check=False,
            creationflags=0x08000000,
        )
    except Exception as e:
        log(f"Shortcut creation note: {e}")

    return f'"{INSTALLED_EXE_PATH}"'


def register_file_associations():
    """Registers Office file extensions and custom icon.ico in the Windows Registry."""
    exe_cmd = install_to_safe_location()
    prog_id = "GoogleDocsOpener.File"
    icon_ref = f'"{INSTALLED_ICON_PATH}",0' if os.path.exists(INSTALLED_ICON_PATH) else f'"{INSTALLED_EXE_PATH}",0'

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Google Docs Cloud Document")
            winreg.SetValueEx(key, "FriendlyTypeName", 0, winreg.REG_SZ, "Google Docs Cloud Document")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\DefaultIcon") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, icon_ref)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open") as key:
            winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, "Google Docs Opener")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'{exe_cmd} "%1"')

        app_base = r"Software\Classes\Applications\GoogleDocsOpener.exe"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, app_base) as key:
            winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, "Google Docs Opener")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_base}\DefaultIcon") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, icon_ref)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_base}\shell\open\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'{exe_cmd} "%1"')

        for ext in MIME_MAP.keys():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}") as key:
                winreg.SetValue(key, "", winreg.REG_SZ, prog_id)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids") as key:
                winreg.SetValueEx(key, prog_id, 0, winreg.REG_NONE, b"")

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        log("Registered file associations and DefaultIcon in HKCU.")
        return True
    except Exception as e:
        log(f"Registry error: {e}")
        return False


def unregister_file_associations():
    """Removes custom file associations from HKCU."""
    prog_id = "GoogleDocsOpener.File"
    try:
        for ext in MIME_MAP.keys():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, prog_id)
            except FileNotFoundError:
                pass
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        log("Unregistered OpenWithProgids file associations.")
        return True
    except Exception as e:
        log(f"Unregister error: {e}")
        return False


def update_windows_autostart(enable, watch_folder=""):
    exe_cmd = install_to_safe_location()
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    val_name = "GoogleDocsOpenerWatcher"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
            if enable and watch_folder and os.path.isdir(watch_folder):
                winreg.SetValueEx(key, val_name, 0, winreg.REG_SZ, f'{exe_cmd} --watch "{watch_folder}"')
                log(f"Autostart enabled for folder: {watch_folder}")
            else:
                try:
                    winreg.DeleteValue(key, val_name)
                    log("Autostart entry removed.")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        log(f"Autostart registry error: {e}")
        return False


# ==============================================================================
# GOOGLE DRIVE API, FOLDER ORGANIZATION & UPLOAD
# ==============================================================================
def get_drive_service(interactive_setup=True):
    if not os.path.exists(CREDS_FILE):
        found, _ = auto_scan_for_credentials()
        if not found:
            if interactive_setup:
                show_message(
                    "Setup Required",
                    "Google 'credentials.json' is not set up yet.\n\nOpening the Control Panel so you can complete setup...",
                    0x40,
                )
                launch_control_panel()
                sys.exit(0)
            else:
                raise FileNotFoundError("credentials.json is missing. Please complete Step 1 first.")

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
            except Exception as e:
                log(f"Token refresh failed (likely 7-day Testing mode expiry): {e}")
                creds = None
                try:
                    os.remove(TOKEN_FILE)
                except Exception:
                    pass

        if not creds:
            log("Opening browser for Google Account login...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0, timeout_seconds=150)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def cloud_file_still_exists(service, file_id):
    if not file_id:
        return False
    try:
        meta = service.files().get(fileId=file_id, fields="id, trashed").execute()
        return not meta.get("trashed", False)
    except Exception:
        return False


def get_or_create_drive_folder(service, cfg):
    """Returns the Google Drive folder ID if dedicated folder mode is enabled."""
    if not cfg.get("use_drive_folder", True):
        return None

    folder_name = (cfg.get("drive_folder_name") or "Google Docs Opener Uploads").strip()
    if not folder_name:
        return None

    cached_id = cfg.get("drive_folder_id", "").strip()
    if cached_id and cloud_file_still_exists(service, cached_id):
        return cached_id

    try:
        safe_name = folder_name.replace("'", "\\'")
        query = (
            f"mimeType='application/vnd.google-apps.folder' "
            f"and name='{safe_name}' and trashed=false"
        )
        res = service.files().list(q=query, spaces="drive", fields="files(id, name)", pageSize=1).execute()
        files = res.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
            created = service.files().create(body=meta, fields="id").execute()
            folder_id = created.get("id")
            log(f"Created dedicated Google Drive folder '{folder_name}'")

        cfg["drive_folder_id"] = folder_id
        save_config(cfg)
        return folder_id
    except Exception as e:
        log(f"Could not resolve Drive folder '{folder_name}', uploading to root instead: {e}")
        return None


def upload_and_open_worker(file_path, open_in_browser=True, force_reupload=False, status_callback=None):
    """Core logic to check SHA-256 cache, upload/convert to Google Drive, and open in browser."""
    def notify(msg):
        log(msg)
        if status_callback:
            status_callback(msg)

    file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File does not exist:\n{file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in MIME_MAP:
        raise ValueError(
            f"Unsupported file extension '{ext}'.\nSupported: {', '.join(MIME_MAP.keys())}"
        )

    notify("Calculating SHA-256 file signature...")
    file_hash = get_file_sha256(file_path)
    cache = load_cache()

    notify("Connecting to Google Drive...")
    service = get_drive_service(interactive_setup=False)

    if not force_reupload and file_hash in cache:
        entry = cache[file_hash]
        cached_id = entry.get("id") if isinstance(entry, dict) else None
        cached_url = entry.get("url") if isinstance(entry, dict) else entry

        notify("Checking if cached document still exists on Google Drive...")
        if cached_id and cloud_file_still_exists(service, cached_id):
            notify("Found existing Google Doc in cache! Opening browser...")
            if open_in_browser:
                open_url(cached_url)
            return cached_url

    g_mime, source_mime, doc_kind = MIME_MAP[ext]
    cfg = load_config()
    parent_id = get_or_create_drive_folder(service, cfg)

    fname = os.path.basename(file_path)
    notify(f"Uploading & converting '{fname}' to Google {doc_kind}...")

    file_metadata = {"name": fname, "mimeType": g_mime}
    if parent_id:
        file_metadata["parents"] = [parent_id]

    media = MediaFileUpload(file_path, mimetype=source_mime, resumable=True)
    uploaded = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )

    uploaded_id = uploaded.get("id")
    doc_url = uploaded.get("webViewLink") or f"https://drive.google.com/open?id={uploaded_id}"

    cache[file_hash] = {
        "id": uploaded_id,
        "url": doc_url,
        "last_known_name": fname,
        "local_path": file_path,
        "doc_type": doc_kind,
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_cache(cache)

    notify("Upload complete! Launching browser...")
    if open_in_browser:
        open_url(doc_url)

    if cfg.get("delete_local_after_upload", False):
        try:
            os.remove(file_path)
            log("Deleted local file after upload (per user setting).")
        except Exception as e:
            log(f"Could not delete local file: {e}")

    return doc_url


def process_file_with_gui_progress(file_path, open_in_browser=True):
    """Displays a sleek modern progress dialog while uploading a file opened from Explorer."""
    if not os.path.exists(CREDS_FILE):
        found, _ = auto_scan_for_credentials()
        if not found:
            show_message(
                "Setup Required",
                "Google 'credentials.json' is not set up yet.\n\nOpening the Setup Control Panel now...",
                0x40,
            )
            launch_control_panel(initial_file=file_path)
            return

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Google Docs Opener")
    root.resizable(False, False)
    root.configure(bg="#0F172A")
    apply_window_icon(root)

    win_w, win_h = 460, 175
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{win_w}x{win_h}+{(sw - win_w) // 2}+{(sh - win_h) // 2}")
    root.attributes("-topmost", True)

    card = tk.Frame(root, bg="#0F172A", padx=24, pady=20)
    card.pack(fill="both", expand=True)

    fname = os.path.basename(file_path)
    tk.Label(
        card,
        text="Opening in Google Docs...",
        font=("Segoe UI", 12, "bold"),
        fg="#F8FAFC",
        bg="#0F172A",
    ).pack(anchor="w")

    tk.Label(
        card,
        text=fname if len(fname) <= 52 else fname[:49] + "...",
        font=("Segoe UI", 9),
        fg="#38BDF8",
        bg="#0F172A",
    ).pack(anchor="w", pady=(2, 12))

    status_var = tk.StringVar(value="Preparing file...")
    tk.Label(
        card,
        textvariable=status_var,
        font=("Segoe UI", 9),
        fg="#CBD5E1",
        bg="#0F172A",
    ).pack(anchor="w", pady=(0, 8))

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Upload.Horizontal.TProgressbar",
        troughcolor="#1E293B",
        background="#38BDF8",
        bordercolor="#0F172A",
        lightcolor="#38BDF8",
        darkcolor="#0284C7",
        thickness=8,
    )

    pb = ttk.Progressbar(card, style="Upload.Horizontal.TProgressbar", mode="indeterminate")
    pb.pack(fill="x")
    pb.start(12)

    result = {"error": None}

    def update_status(msg):
        root.after(0, lambda: status_var.set(msg))

    def worker():
        try:
            upload_and_open_worker(
                file_path,
                open_in_browser=open_in_browser,
                force_reupload=False,
                status_callback=update_status,
            )
        except Exception as e:
            result["error"] = (e, traceback.format_exc())
        finally:
            root.after(250, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()

    if result["error"]:
        err_obj, err_trace = result["error"]
        log(f"Error processing file '{file_path}':\n{err_trace}")
        show_message("Google Docs Opener — Upload Error", f"Could not open file:\n\n{err_obj}", 0x10)


# ==============================================================================
# BACKGROUND FOLDER WATCHER (SINGLE-INSTANCE SAFE)
# ==============================================================================
def is_file_ready(file_path):
    """Checks that a newly dropped file has finished copying and is not locked."""
    try:
        size1 = os.path.getsize(file_path)
        if size1 == 0:
            return False
        time.sleep(1.0)
        size2 = os.path.getsize(file_path)
        if size1 != size2:
            return False
        with open(file_path, "rb"):
            return True
    except Exception:
        return False


def watch_folder_loop(folder_path):
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\GoogleDocsOpenerWatcherMutex_v2")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("Background watcher is already running. Exiting duplicate instance.")
        return

    log(f"Watching folder in background: {folder_path}")
    seen_files = set()
    if os.path.exists(folder_path):
        for fname in os.listdir(folder_path):
            seen_files.add(os.path.join(folder_path, fname))

    while True:
        try:
            if os.path.exists(folder_path):
                for fname in os.listdir(folder_path):
                    if fname.startswith("~$") or fname.startswith(".~") or fname.endswith(".tmp"):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in MIME_MAP:
                        full_path = os.path.join(folder_path, fname)
                        if full_path not in seen_files and os.path.isfile(full_path):
                            if is_file_ready(full_path):
                                seen_files.add(full_path)
                                try:
                                    upload_and_open_worker(full_path, open_in_browser=True)
                                except Exception as e:
                                    log(f"Watcher upload error for {full_path}: {e}")
        except Exception as e:
            log(f"Watcher loop error: {e}")
        time.sleep(4)


# ==============================================================================
# TUTORIAL & TROUBLESHOOTING TEXT
# ==============================================================================
TUTORIAL_AND_MISTAKES_TEXT = """====================================================================
STEP-BY-STEP: HOW TO GET YOUR FREE GOOGLE 'credentials.json' (2 MINS)
====================================================================
Why isn't this pre-installed? Google immediately revokes API keys uploaded to public repositories. Creating your own personal key takes 2 minutes, is 100% free (no credit card required), and you only do it once:

1. Click 'Open Google Cloud Console' on the Setup tab and sign in with your Google Account.
2. Click 'Select a project' (top-left) -> 'New Project' -> Name it 'DocsOpener' -> 'Create' -> 'Select Project'.
3. Go to 'APIs & Services' -> 'Library' -> search for 'Google Drive API' -> click 'Enable'.
4. Go to 'Google Auth Platform' (or 'OAuth consent screen') -> 'Overview' -> 'Get Started':
   - App Name: DocsOpener | Support Email: Your Gmail -> Next
   - Audience: Select 'External' -> Next
   - Contact Email: Your Gmail -> Finish & Create.
5. UNLOCK 'PUBLISH APP' (Prevents your login from expiring every 7 days!):
   - Click 'Branding' in the left sidebar.
   - Under 'App domain', paste https://example.com into BOTH 'Application home page' and 'Application privacy policy link'.
   - Under 'Authorized domains', click '+ Add domain' and type: example.com
   - Click 'Save'. Then go to 'Audience' -> click 'Publish app' -> 'Confirm'.
6. DOWNLOAD YOUR JSON FILE:
   - Click 'Clients' (or 'Credentials') in the left sidebar -> '+ Create Client' (or '+ Create Credentials' -> 'OAuth client ID').
   - Application type: MUST BE 'Desktop app' (Do NOT pick Web Application!).
   - Click 'Create' -> Click 'Download JSON'.
7. Back in this app, click 'Auto-Scan Downloads' (or 'Browse JSON...')!

====================================================================
COMMON MISTAKES & HOW TO FIX THEM
====================================================================

1. Where is GoogleDocsOpener.exe stored? Can I delete it from Downloads?
   When you click '1-Click Safe Install & Register Office Files', this app automatically copies itself and your icon.ico into:
   %LOCALAPPDATA%\\GoogleDocsOpener
   All tokens, caches, and logs live there safely. You can delete the .exe from Downloads afterward and reopen the Control Panel anytime from your Windows Start Menu ('Google Docs Opener').

2. Browser says 'Error 403: access_denied' when logging into Google
   Your Google Cloud project is still in 'Testing' mode without your email listed. Go to Google Cloud Console -> 'Audience' -> click 'Publish App' (recommended), OR add your Gmail under 'Test users'.

3. It worked for 7 days, then asked me to log in again ('invalid_grant')
   Apps left in 'Testing' mode expire tokens every 7 days. Follow Step 5 above to click 'Publish App'.

4. Browser says 'Google hasn't verified this app' during first login
   Completely normal because you just created the project under your own account! Click 'Advanced' (bottom-left) -> 'Go to DocsOpener (unsafe)' -> 'Continue'.

5. Windows 11 still opens Word or WordPad instead of GoogleDocsOpener
   Windows 11 protects default app associations. Click '1-Click Safe Install & Register', then right-click any .docx file once -> 'Open with' -> 'Choose another app' -> select 'Google Docs Opener' -> click 'Always'.
"""


# ==============================================================================
# MODERN CONTROL PANEL GUI
# ==============================================================================
def launch_control_panel(initial_file=None):
    import tkinter as tk
    from tkinter import ttk, filedialog

    auto_scan_for_credentials()
    sync_icon_to_appdata()
    cfg = load_config()

    C_SIDEBAR = "#0F172A"
    C_SIDEBAR_HOVER = "#1E293B"
    C_SIDEBAR_ACTIVE = "#0284C7"
    C_BG = "#F1F5F9"
    C_CARD = "#FFFFFF"
    C_BORDER = "#E2E8F0"
    C_TEXT = "#0F172A"
    C_MUTED = "#64748B"
    C_SUCCESS_BG = "#DCFCE7"
    C_SUCCESS_FG = "#15803D"
    C_WARN_BG = "#FEF3C7"
    C_WARN_FG = "#B45309"
    C_DANGER_BG = "#FEE2E2"
    C_DANGER_FG = "#B91C1C"

    root = tk.Tk()
    root.title(f"Google Docs Opener v{APP_VERSION} — Control Center")
    root.configure(bg=C_BG)
    root.resizable(True, True)
    apply_window_icon(root)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", font=("Segoe UI", 9), background=C_BG, foreground=C_TEXT)
    style.configure("Card.TFrame", background=C_CARD)
    style.configure("CardTitle.TLabel", background=C_CARD, foreground=C_TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("CardBody.TLabel", background=C_CARD, foreground=C_MUTED, font=("Segoe UI", 9))
    style.configure("CardCheck.TCheckbutton", background=C_CARD, foreground=C_TEXT, font=("Segoe UI", 9))
    style.map("CardCheck.TCheckbutton", background=[("active", C_CARD)])

    style.configure(
        "Accent.TButton",
        font=("Segoe UI", 9, "bold"),
        background="#0284C7",
        foreground="#FFFFFF",
        borderwidth=0,
        focusthickness=0,
        padding=(14, 7),
    )
    style.map("Accent.TButton", background=[("active", "#0369A1"), ("disabled", "#94A3B8")])

    style.configure(
        "Secondary.TButton",
        font=("Segoe UI", 9),
        background="#F8FAFC",
        foreground="#1E293B",
        bordercolor="#CBD5E1",
        borderwidth=1,
        focusthickness=0,
        padding=(12, 6),
    )
    style.map("Secondary.TButton", background=[("active", "#E2E8F0")])

    style.configure(
        "Treeview",
        font=("Segoe UI", 9),
        rowheight=28,
        background="#FFFFFF",
        fieldbackground="#FFFFFF",
        foreground=C_TEXT,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        font=("Segoe UI", 9, "bold"),
        background="#E2E8F0",
        foreground="#1E293B",
        padding=(8, 6),
    )
    style.map("Treeview", background=[("selected", "#E0F2FE")], foreground=[("selected", "#0369A1")])

    shell = tk.Frame(root, bg=C_BG)
    shell.pack(fill="both", expand=True)

    sidebar = tk.Frame(shell, bg=C_SIDEBAR, width=230)
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    main_col = tk.Frame(shell, bg=C_BG)
    main_col.pack(side="left", fill="both", expand=True)

    brand_box = tk.Frame(sidebar, bg=C_SIDEBAR, padx=20, pady=22)
    brand_box.pack(fill="x")
    tk.Label(
        brand_box,
        text="Google Docs Opener",
        font=("Segoe UI", 13, "bold"),
        fg="#F8FAFC",
        bg=C_SIDEBAR,
    ).pack(anchor="w")
    tk.Label(
        brand_box,
        text="Cloud Document Bridge",
        font=("Segoe UI", 9),
        fg="#38BDF8",
        bg=C_SIDEBAR,
    ).pack(anchor="w", pady=(2, 0))

    cta_box = tk.Frame(sidebar, bg=C_SIDEBAR, padx=16, pady=6)
    cta_box.pack(fill="x")

    status_bar_var = tk.StringVar(value=f"Storage: {APP_DATA_DIR}")

    def set_status_bar(msg):
        status_bar_var.set(msg)

    def on_upload_file_dialog(file_to_open=None):
        target = file_to_open
        if not target:
            exts = " ".join(f"*{k}" for k in MIME_MAP.keys())
            target = filedialog.askopenfilename(
                title="Select Office Document to Upload & Open in Google Docs",
                filetypes=[("Office & OpenDocument Files", exts), ("All Files", "*.*")],
            )
        if not target:
            return
        if not os.path.exists(CREDS_FILE):
            show_message("Missing Credentials", "Please complete Step 1 (credentials.json) first!", 0x30)
            switch_page("setup")
            return

        set_status_bar(f"Uploading '{os.path.basename(target)}' to Google Drive...")

        def run_upload():
            try:
                upload_and_open_worker(
                    target,
                    open_in_browser=True,
                    status_callback=lambda m: root.after(0, lambda: set_status_bar(m)),
                )
                root.after(0, lambda: [refresh_all_status(), refresh_history_table(), set_status_bar(f"Opened '{os.path.basename(target)}' in browser.")])
            except Exception as e:
                err_txt = traceback.format_exc()
                log(f"Manual upload error:\n{err_txt}")
                root.after(0, lambda: [set_status_bar("Upload failed. Check Diagnostics tab."), show_message("Upload Error", str(e), 0x10)])

        threading.Thread(target=run_upload, daemon=True).start()

    btn_quick_open = tk.Button(
        cta_box,
        text="+ Upload & Open File...",
        font=("Segoe UI", 9, "bold"),
        bg="#38BDF8",
        fg="#0F172A",
        activebackground="#0284C7",
        activeforeground="#FFFFFF",
        relief="flat",
        cursor="hand2",
        padx=12,
        pady=8,
        command=on_upload_file_dialog,
    )
    btn_quick_open.pack(fill="x", pady=(0, 12))

    nav_buttons = {}
    pages = {}

    content_header = tk.Frame(main_col, bg="#FFFFFF", height=64, padx=24, pady=12)
    content_header.pack(fill="x")
    content_header.pack_propagate(False)
    tk.Frame(main_col, bg=C_BORDER, height=1).pack(fill="x")

    page_title_var = tk.StringVar(value="Quick Setup & Status")
    tk.Label(
        content_header,
        textvariable=page_title_var,
        font=("Segoe UI", 14, "bold"),
        bg="#FFFFFF",
        fg=C_TEXT,
    ).pack(side="left")

    badge_frame = tk.Frame(content_header, bg="#FFFFFF")
    badge_frame.pack(side="right")

    lbl_badge_creds = tk.Label(badge_frame, text="Credentials", font=("Segoe UI", 8, "bold"), padx=10, pady=4)
    lbl_badge_creds.pack(side="left", padx=4)

    lbl_badge_auth = tk.Label(badge_frame, text="Account", font=("Segoe UI", 8, "bold"), padx=10, pady=4)
    lbl_badge_auth.pack(side="left", padx=4)

    page_container = tk.Frame(main_col, bg=C_BG, padx=24, pady=18)
    page_container.pack(fill="both", expand=True)

    tk.Frame(main_col, bg=C_BORDER, height=1).pack(fill="x", side="bottom")
    status_bar = tk.Frame(main_col, bg="#FFFFFF", height=30, padx=16)
    status_bar.pack(fill="x", side="bottom")
    tk.Label(status_bar, textvariable=status_bar_var, font=("Segoe UI", 8), bg="#FFFFFF", fg=C_MUTED).pack(side="left", pady=4)
    tk.Label(status_bar, text=f"v{APP_VERSION}", font=("Segoe UI", 8, "bold"), bg="#FFFFFF", fg=C_MUTED).pack(side="right", pady=4)

    def make_card(parent, title, subtitle=""):
        outer = tk.Frame(parent, bg=C_BORDER, padx=1, pady=1)
        outer.pack(fill="x", pady=6)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=16)
        inner.pack(fill="both", expand=True)
        head = ttk.Frame(inner, style="Card.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")
        if subtitle:
            ttk.Label(inner, text=subtitle, style="CardBody.TLabel", wraplength=680).pack(anchor="w", pady=(0, 10))
        return inner, head

    # ------------------------------------------------------------------
    # PAGE 1: SETUP & QUICK INSTALL
    # ------------------------------------------------------------------
    p_setup = tk.Frame(page_container, bg=C_BG)
    pages["setup"] = ("Setup & Quick Install", p_setup)

    c1, c1_head = make_card(
        p_setup,
        "Step 1 — Google Cloud Credentials (credentials.json)",
        "Import your OAuth 2.0 Desktop Client JSON file once. It is stored safely inside AppData.",
    )
    lbl_step1_pill = tk.Label(c1_head, text="", font=("Segoe UI", 8, "bold"), padx=8, pady=2)
    lbl_step1_pill.pack(side="right")

    def on_auto_scan():
        ok, msg = auto_scan_for_credentials()
        refresh_all_status()
        if ok:
            set_status_bar("Imported credentials.json from Downloads.")
            show_message("Credentials Found!", msg)
        else:
            show_message(
                "Not Found in Downloads",
                "Could not find 'client_secret*.json' or 'credentials.json' in your Downloads folder.\n\n"
                "Click 'Browse JSON File...' to select it manually, or open the 'Setup Guide & FAQ' tab.",
                0x40,
            )

    def on_browse_json():
        selected = filedialog.askopenfilename(
            title="Select downloaded Google OAuth Desktop JSON file",
            initialdir=os.path.join(os.path.expanduser("~"), "Downloads"),
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
        )
        if selected:
            ok, msg = validate_and_import_credentials(selected)
            refresh_all_status()
            show_message("Import Result", msg, 0x40 if ok else 0x10)

    row_c1 = ttk.Frame(c1, style="Card.TFrame")
    row_c1.pack(fill="x")
    ttk.Button(
        row_c1,
        text="1. Open Google Cloud Console",
        style="Secondary.TButton",
        command=lambda: open_url("https://console.cloud.google.com/apis/credentials"),
    ).pack(side="left", padx=(0, 8))
    ttk.Button(row_c1, text="2. Auto-Scan Downloads", style="Accent.TButton", command=on_auto_scan).pack(side="left", padx=(0, 8))
    ttk.Button(row_c1, text="3. Browse JSON File...", style="Secondary.TButton", command=on_browse_json).pack(side="left", padx=(0, 8))
    ttk.Button(row_c1, text="Read 2-Min Guide ->", style="Secondary.TButton", command=lambda: switch_page("help")).pack(side="right")

    c2, c2_head = make_card(
        p_setup,
        "Step 2 — Connect Your Google Drive Account",
        "Sign in with your Google Account in the browser to authorize file uploads and conversions.",
    )
    lbl_step2_pill = tk.Label(c2_head, text="", font=("Segoe UI", 8, "bold"), padx=8, pady=2)
    lbl_step2_pill.pack(side="right")

    def on_login():
        if not os.path.exists(CREDS_FILE):
            show_message("Missing Credentials", "Please complete Step 1 first!", 0x10)
            return
        set_status_bar("Waiting for Google login in your browser...")

        def login_worker():
            try:
                get_drive_service(interactive_setup=False)
                root.after(0, lambda: [refresh_all_status(), set_status_bar("Google Drive connected!"), show_message("Success!", "Your Google Drive account is connected and ready!")])
            except Exception as e:
                err_txt = traceback.format_exc()
                log(f"Login error:\n{err_txt}")
                root.after(0, lambda: [set_status_bar("Login failed."), show_message("Login Error", f"Failed to log in:\n{e}", 0x10)])

        threading.Thread(target=login_worker, daemon=True).start()

    def on_reset_token():
        if os.path.exists(TOKEN_FILE):
            try:
                os.remove(TOKEN_FILE)
            except Exception:
                pass
        refresh_all_status()
        set_status_bar("Cleared saved Google account token.")
        show_message("Logged Out", "Cleared saved login token. Click 'Sign In / Verify Connection' to connect an account.")

    row_c2 = ttk.Frame(c2, style="Card.TFrame")
    row_c2.pack(fill="x")
    ttk.Button(row_c2, text="Sign In / Verify Connection", style="Accent.TButton", command=on_login).pack(side="left", padx=(0, 8))
    ttk.Button(row_c2, text="Switch Account / Sign Out", style="Secondary.TButton", command=on_reset_token).pack(side="left")

    c3, c3_head = make_card(
        p_setup,
        "Step 3 — 1-Click Safe Install & Windows File Associations",
        "Copies GoogleDocsOpener.exe and icon.ico into %LOCALAPPDATA%\\GoogleDocsOpener, creates a Start Menu shortcut, and registers .docx, .xlsx, .pptx, .odt, .ods, .odp, .rtf, and .csv.",
    )
    lbl_step3_pill = tk.Label(c3_head, text="", font=("Segoe UI", 8, "bold"), padx=8, pady=2)
    lbl_step3_pill.pack(side="right")

    def on_install_associations():
        ok = register_file_associations()
        refresh_all_status()
        if ok:
            set_status_bar("Installed to AppData & registered file associations.")
            show_message(
                "Installed & Registered!",
                f"1. Permanent copy installed to:\n   {INSTALLED_EXE_PATH}\n\n"
                "2. Custom icon.ico & Start Menu shortcut configured.\n\n"
                "3. Registered Office extensions (.docx, .xlsx, .pptx, etc.).\n"
                "   Tip: On Windows 11, right-click a .docx file once -> 'Open with' -> 'Choose another app' -> select 'Google Docs Opener' -> click 'Always'.",
            )

    def on_unregister():
        if unregister_file_associations():
            set_status_bar("Removed file associations from registry.")
            show_message("Unregistered", "Removed OpenWith file associations from Windows Registry.")

    row_c3 = ttk.Frame(c3, style="Card.TFrame")
    row_c3.pack(fill="x")
    ttk.Button(row_c3, text="1-Click Safe Install & Register Files", style="Accent.TButton", command=on_install_associations).pack(side="left", padx=(0, 8))
    ttk.Button(row_c3, text="Windows Default Apps Settings", style="Secondary.TButton", command=lambda: os.startfile("ms-settings:defaultapps")).pack(side="left", padx=(0, 8))
    ttk.Button(row_c3, text="Open AppData Folder", style="Secondary.TButton", command=lambda: os.startfile(APP_DATA_DIR)).pack(side="left", padx=(0, 8))
    ttk.Button(row_c3, text="Unregister", style="Secondary.TButton", command=on_unregister).pack(side="right")

    # ------------------------------------------------------------------
    # PAGE 2: RECENT CLOUD DOCUMENTS (UPLOAD HISTORY)
    # ------------------------------------------------------------------
    p_history = tk.Frame(page_container, bg=C_BG)
    pages["history"] = ("Recent Cloud Documents", p_history)

    c_hist, _ = make_card(
        p_history,
        "Uploaded Google Docs, Sheets & Slides History",
        "Double-click any entry to open it immediately in your browser, or copy its Google Docs link.",
    )

    tree_frame = ttk.Frame(c_hist, style="Card.TFrame")
    tree_frame.pack(fill="both", expand=True, pady=(0, 10))

    cols = ("name", "type", "date", "url")
    tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=11)
    tree.heading("name", text="Document Name")
    tree.heading("type", text="Type")
    tree.heading("date", text="Uploaded At")
    tree.heading("url", text="Google Drive Link")

    tree.column("name", width=240, anchor="w")
    tree.column("type", width=95, anchor="center")
    tree.column("date", width=135, anchor="center")
    tree.column("url", width=230, anchor="w")

    tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree_scroll.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    def refresh_history_table():
        for item in tree.get_children():
            tree.delete(item)
        cache = load_cache()
        entries = []
        for fhash, info in cache.items():
            if isinstance(info, dict):
                entries.append((
                    fhash,
                    info.get("last_known_name", "Unknown Document"),
                    info.get("doc_type", "Document"),
                    info.get("uploaded_at", "—"),
                    info.get("url", ""),
                ))
            else:
                entries.append((fhash, "Cached Document", "Document", "—", str(info)))
        entries.sort(key=lambda x: x[3], reverse=True)
        for fhash, name, dtype, dt, url in entries:
            tree.insert("", "end", iid=fhash, values=(name, dtype, dt, url))

    def get_selected_history_url():
        sel = tree.selection()
        if not sel:
            show_message("No Selection", "Please select a document in the table first.")
            return None, None
        vals = tree.item(sel[0], "values")
        return sel[0], vals[3] if len(vals) >= 4 else None

    def on_open_selected_doc(event=None):
        _, url = get_selected_history_url()
        if url:
            open_url(url)

    def on_copy_selected_link():
        _, url = get_selected_history_url()
        if url:
            root.clipboard_clear()
            root.clipboard_append(url)
            set_status_bar("Copied Google Docs URL to clipboard!")

    def on_remove_selected_cache():
        fhash, _ = get_selected_history_url()
        if fhash:
            cache = load_cache()
            cache.pop(fhash, None)
            save_cache(cache)
            refresh_history_table()
            set_status_bar("Removed selected entry from local upload cache.")

    def on_clear_all_cache():
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        refresh_history_table()
        set_status_bar("Cleared entire SHA-256 upload cache.")
        show_message("Cache Cleared", "Cleared SHA-256 upload history cache.")

    tree.bind("<Double-1>", on_open_selected_doc)

    hist_btns = ttk.Frame(c_hist, style="Card.TFrame")
    hist_btns.pack(fill="x")
    ttk.Button(hist_btns, text="Open in Browser", style="Accent.TButton", command=on_open_selected_doc).pack(side="left", padx=(0, 8))
    ttk.Button(hist_btns, text="Copy Link", style="Secondary.TButton", command=on_copy_selected_link).pack(side="left", padx=(0, 8))
    ttk.Button(hist_btns, text="Remove Selected", style="Secondary.TButton", command=on_remove_selected_cache).pack(side="left", padx=(0, 8))
    ttk.Button(hist_btns, text="Clear All Cache", style="Secondary.TButton", command=on_clear_all_cache).pack(side="right")

    # ------------------------------------------------------------------
    # PAGE 3: SETTINGS & FOLDER WATCHER
    # ------------------------------------------------------------------
    p_settings = tk.Frame(page_container, bg=C_BG)
    pages["settings"] = ("Preferences & Folder Watcher", p_settings)

    c_prefs, _ = make_card(
        p_settings,
        "Browser & Google Drive Organization",
        "Customize which browser opens your documents and how files are organized inside Google Drive.",
    )

    grid_p = ttk.Frame(c_prefs, style="Card.TFrame")
    grid_p.pack(fill="x", pady=(0, 6))

    ttk.Label(grid_p, text="Preferred Browser:", style="CardBody.TLabel").grid(row=0, column=0, sticky="w", pady=6)
    browser_var = tk.StringVar(value=cfg.get("preferred_browser", "firefox"))
    cb_browser = ttk.Combobox(
        grid_p,
        textvariable=browser_var,
        values=["firefox", "chrome", "edge", "brave", "vivaldi", "default"],
        state="readonly",
        width=22,
    )
    cb_browser.grid(row=0, column=1, sticky="w", padx=14, pady=6)

    use_folder_var = tk.BooleanVar(value=cfg.get("use_drive_folder", True))
    ttk.Checkbutton(
        grid_p,
        text="Organize uploaded files inside a dedicated Google Drive folder:",
        variable=use_folder_var,
        style="CardCheck.TCheckbutton",
    ).grid(row=1, column=0, sticky="w", pady=6)

    drive_folder_name_var = tk.StringVar(value=cfg.get("drive_folder_name", "Google Docs Opener Uploads"))
    ttk.Entry(grid_p, textvariable=drive_folder_name_var, width=32).grid(row=1, column=1, sticky="w", padx=14, pady=6)

    del_var = tk.BooleanVar(value=cfg.get("delete_local_after_upload", False))
    ttk.Checkbutton(
        grid_p,
        text="Delete local Office file from PC after uploading to Google Drive",
        variable=del_var,
        style="CardCheck.TCheckbutton",
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=6)

    c_watch, _ = make_card(
        p_settings,
        "Background Folder Watcher & Windows Autostart",
        "Automatically upload & open any new Office file dropped into your chosen watch folder.",
    )

    watch_enabled_var = tk.BooleanVar(value=cfg.get("autostart_watcher", False))
    watch_folder_var = tk.StringVar(value=cfg.get("watch_folder", ""))

    ttk.Checkbutton(
        c_watch,
        text="Start Watcher automatically with Windows and monitor this folder:",
        variable=watch_enabled_var,
        style="CardCheck.TCheckbutton",
    ).pack(anchor="w", pady=(0, 8))

    watch_row = ttk.Frame(c_watch, style="Card.TFrame")
    watch_row.pack(fill="x", pady=(0, 10))
    ttk.Entry(watch_row, textvariable=watch_folder_var).pack(side="left", fill="x", expand=True, padx=(0, 8))

    def on_pick_folder():
        d = filedialog.askdirectory(title="Select Folder to Watch Automatically")
        if d:
            watch_folder_var.set(d)

    ttk.Button(watch_row, text="Browse Folder...", style="Secondary.TButton", command=on_pick_folder).pack(side="right")

    def on_save_settings():
        old_folder_name = cfg.get("drive_folder_name", "")
        new_folder_name = drive_folder_name_var.get().strip() or "Google Docs Opener Uploads"
        if old_folder_name != new_folder_name:
            cfg["drive_folder_id"] = ""

        cfg["preferred_browser"] = browser_var.get()
        cfg["use_drive_folder"] = use_folder_var.get()
        cfg["drive_folder_name"] = new_folder_name
        cfg["delete_local_after_upload"] = del_var.get()
        cfg["autostart_watcher"] = watch_enabled_var.get()
        cfg["watch_folder"] = watch_folder_var.get().strip()
        save_config(cfg)
        update_windows_autostart(cfg["autostart_watcher"], cfg["watch_folder"])
        set_status_bar("All settings saved!")
        show_message("Settings Saved", "Your preferences and Windows Autostart settings have been updated!")

    def on_launch_watcher_now():
        wf = watch_folder_var.get().strip()
        if not wf or not os.path.isdir(wf):
            show_message("Invalid Folder", "Please select a valid folder to watch first.", 0x30)
            return
        on_save_settings()
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--watch", wf], creationflags=0x08000000)
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch", wf], creationflags=0x08000000)
        set_status_bar(f"Background watcher started for: {wf}")
        show_message("Watcher Started", f"Background watcher is now actively monitoring:\n{wf}")

    save_row = ttk.Frame(p_settings)
    save_row.pack(fill="x", pady=6)
    ttk.Button(save_row, text="Save All Settings", style="Accent.TButton", command=on_save_settings).pack(side="left", padx=(0, 8))
    ttk.Button(save_row, text="Start Watcher in Background Now", style="Secondary.TButton", command=on_launch_watcher_now).pack(side="left")

    # ------------------------------------------------------------------
    # PAGE 4: DIAGNOSTICS & LIVE LOGS
    # ------------------------------------------------------------------
    p_logs = tk.Frame(page_container, bg=C_BG)
    pages["logs"] = ("Diagnostics & Activity Log", p_logs)

    c_log, _ = make_card(
        p_logs,
        "Diagnostic Log Viewer (python_log.txt)",
        "Inspect recent activity, OAuth events, and error tracebacks without leaving the app.",
    )

    log_box_frame = ttk.Frame(c_log, style="Card.TFrame")
    log_box_frame.pack(fill="both", expand=True, pady=(0, 10))

    txt_log = tk.Text(
        log_box_frame,
        wrap="word",
        font=("Consolas", 9),
        bg="#0F172A",
        fg="#E2E8F0",
        insertbackground="#FFFFFF",
        padx=12,
        pady=10,
        height=14,
        relief="flat",
    )
    log_scroll = ttk.Scrollbar(log_box_frame, orient="vertical", command=txt_log.yview)
    txt_log.configure(yscrollcommand=log_scroll.set)
    log_scroll.pack(side="right", fill="y")
    txt_log.pack(side="left", fill="both", expand=True)

    def refresh_log_viewer():
        txt_log.configure(state="normal")
        txt_log.delete("1.0", "end")
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    txt_log.insert("1.0", f.read())
                txt_log.see("end")
            except Exception as e:
                txt_log.insert("1.0", f"Could not read log file: {e}")
        else:
            txt_log.insert("1.0", "Log file is currently empty.")
        txt_log.configure(state="disabled")

    log_btns = ttk.Frame(c_log, style="Card.TFrame")
    log_btns.pack(fill="x")
    ttk.Button(log_btns, text="Refresh Log", style="Accent.TButton", command=refresh_log_viewer).pack(side="left", padx=(0, 8))
    ttk.Button(log_btns, text="Open in Notepad", style="Secondary.TButton", command=lambda: os.startfile(LOG_FILE) if os.path.exists(LOG_FILE) else None).pack(side="left", padx=(0, 8))
    ttk.Button(log_btns, text="Clear Log", style="Secondary.TButton", command=lambda: [clear_log(), refresh_log_viewer()]).pack(side="right")

    # ------------------------------------------------------------------
    # PAGE 5: BUILT-IN TUTORIAL & TROUBLESHOOTING
    # ------------------------------------------------------------------
    p_help = tk.Frame(page_container, bg=C_BG)
    pages["help"] = ("Setup Guide & Troubleshooting FAQ", p_help)

    c_help, _ = make_card(
        p_help,
        "2-Minute Google Cloud Setup & Common Mistakes",
        "Follow these steps once to create your free Desktop OAuth Client ID and publish it so tokens never expire.",
    )

    help_box_frame = ttk.Frame(c_help, style="Card.TFrame")
    help_box_frame.pack(fill="both", expand=True)

    txt_help = tk.Text(
        help_box_frame,
        wrap="word",
        font=("Segoe UI", 9),
        bg="#F8FAFC",
        fg="#0F172A",
        padx=14,
        pady=12,
        height=16,
        relief="flat",
    )
    help_scroll = ttk.Scrollbar(help_box_frame, orient="vertical", command=txt_help.yview)
    txt_help.configure(yscrollcommand=help_scroll.set)
    help_scroll.pack(side="right", fill="y")
    txt_help.pack(side="left", fill="both", expand=True)
    txt_help.insert("1.0", TUTORIAL_AND_MISTAKES_TEXT)
    txt_help.configure(state="disabled")

    # ------------------------------------------------------------------
    # SIDEBAR NAVIGATION & STATUS REFRESH LOGIC
    # ------------------------------------------------------------------
    nav_items = [
        ("setup", "1. Setup & Install"),
        ("history", "2. Recent Documents"),
        ("settings", "3. Settings & Watcher"),
        ("logs", "4. Diagnostics & Log"),
        ("help", "5. Setup Guide & FAQ"),
    ]

    def switch_page(key):
        for k, (_, frame) in pages.items():
            frame.pack_forget()
        title, target_frame = pages[key]
        page_title_var.set(title)
        target_frame.pack(fill="both", expand=True)

        for k, btn in nav_buttons.items():
            if k == key:
                btn.configure(bg=C_SIDEBAR_ACTIVE, fg="#FFFFFF", font=("Segoe UI", 9, "bold"))
            else:
                btn.configure(bg=C_SIDEBAR, fg="#CBD5E1", font=("Segoe UI", 9))

        if key == "history":
            refresh_history_table()
        elif key == "logs":
            refresh_log_viewer()

    for key, label in nav_items:
        b = tk.Button(
            sidebar,
            text=f"  {label}",
            anchor="w",
            font=("Segoe UI", 9),
            bg=C_SIDEBAR,
            fg="#CBD5E1",
            activebackground=C_SIDEBAR_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=18,
            pady=11,
            cursor="hand2",
            command=lambda k=key: switch_page(k),
        )
        b.pack(fill="x", padx=10, pady=2)
        nav_buttons[key] = b

    def refresh_all_status():
        if os.path.exists(CREDS_FILE):
            lbl_step1_pill.config(text="READY", bg=C_SUCCESS_BG, fg=C_SUCCESS_FG)
            lbl_badge_creds.config(text="JSON: Ready", bg=C_SUCCESS_BG, fg=C_SUCCESS_FG)
        else:
            lbl_step1_pill.config(text="MISSING", bg=C_DANGER_BG, fg=C_DANGER_FG)
            lbl_badge_creds.config(text="JSON: Missing", bg=C_DANGER_BG, fg=C_DANGER_FG)

        if os.path.exists(TOKEN_FILE):
            lbl_step2_pill.config(text="CONNECTED", bg=C_SUCCESS_BG, fg=C_SUCCESS_FG)
            lbl_badge_auth.config(text="Drive: Connected", bg=C_SUCCESS_BG, fg=C_SUCCESS_FG)
        else:
            lbl_step2_pill.config(text="NOT SIGNED IN", bg=C_WARN_BG, fg=C_WARN_FG)
            lbl_badge_auth.config(text="Drive: Sign In Needed", bg=C_WARN_BG, fg=C_WARN_FG)

        if os.path.exists(INSTALLED_EXE_PATH):
            lbl_step3_pill.config(text="INSTALLED IN APPDATA", bg=C_SUCCESS_BG, fg=C_SUCCESS_FG)
        else:
            lbl_step3_pill.config(text="NOT INSTALLED YET", bg=C_WARN_BG, fg=C_WARN_FG)

    refresh_all_status()
    refresh_history_table()
    switch_page("setup")

    root.update_idletasks()
    win_w = min(max(root.winfo_reqwidth() + 40, 960), root.winfo_screenwidth() - 80)
    win_h = min(max(root.winfo_reqheight() + 40, 650), root.winfo_screenheight() - 80)
    pos_x = max((root.winfo_screenwidth() - win_w) // 2, 20)
    pos_y = max((root.winfo_screenheight() - win_h) // 2, 20)
    root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
    root.minsize(860, 580)

    if initial_file and os.path.exists(initial_file) and os.path.exists(CREDS_FILE):
        root.after(400, lambda: on_upload_file_dialog(initial_file))

    root.mainloop()


# ==============================================================================
# ENTRY POINT
# ==============================================================================
def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        import tkinter  # noqa: F401
        assert callable(build) and callable(InstalledAppFlow)
        sys.exit(0)

    init_log()

    if len(sys.argv) < 2 or not sys.argv[1].strip():
        launch_control_panel()
        return

    if sys.argv[1] == "--watch" and len(sys.argv) >= 3:
        watch_folder_loop(sys.argv[2])
        return

    process_file_with_gui_progress(sys.argv[1], open_in_browser=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        log("\n--- UNEXPECTED CRASH REPORT ---\n" + err)
        show_message(
            "Google Docs Opener — Error",
            f"An unexpected error occurred:\n\n{err[:500]}\n\nFull details saved in:\n{LOG_FILE}",
            0x10,
        )
        sys.exit(1)