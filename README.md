# Local Office to Google Docs Opener (Windows)

I wanted a way to double-click local Word, Excel, and PowerPoint files on Windows and have them automatically upload to Google Drive and open in Google Docs, Sheets, or Slides inside my browser (Firefox, Chrome, Edge, Brave, Vivaldi, etc.)—without paying for Microsoft Office or manually dragging files into a browser tab every time.

Wait—no Python setup or `.bat` scripts required anymore! The project now ships as a standalone **`GoogleDocsOpener.exe`** with a built-in **Control Center GUI**, 1-click Windows Registry file association installer, upload history viewer, and an optional background folder watcher.

> **Note / Disclaimer:** The code and documentation in this repo are mainly AI-generated. I didn't write the Python code myself—I just had the idea for the workflow, tested the builds when they crashed (like fixing PyInstaller native DLL loading, Windows registry associations, and German umlaut `ä/ö/ü` encoding bugs), and came up with the logic for how it should behave (like using SHA-256 file hashing so moving files around doesn't cause duplicate uploads).

---

## How it actually works (Read this first)

Before setting this up, there are a few things you should know about how this handles your files:

1. **It won't spam your Google Drive with duplicates:** The first time you double-click a file, it uploads it and saves a digital fingerprint (`SHA-256` hash) of that file's contents in `upload_cache.json`. Next time you double-click that file, it skips the upload and immediately opens the existing Google Doc link (and you can browse or reopen all past uploads anytime in the app's **Recent Documents** tab).
2. **Moving or renaming local files doesn't break the link:** Because it checks the actual contents of the file instead of the folder path, you can move a `.docx` from your `Downloads` folder to your `Desktop` or rename it, and double-clicking it will still open the same Google Doc. (And if you delete the file inside Google Drive later, the app notices it's in the trash and automatically uploads a fresh copy next time you click it.)
3. **Your edits happen in the cloud, NOT on the local file:** Once a file is uploaded, the local `.docx` on your hard drive basically acts like a shortcut to your Google Doc. When you type and make changes inside Google Docs in your browser, those changes are saved in your Google Drive—they are **not** synced back into the old `.docx` file sitting on your PC. If you need to email someone an updated `.docx` later, download it from inside Google Docs (`File` -> `Download` -> `Microsoft Word`).
4. **Everything is stored safely in AppData:** When you click the install button inside the Control Center, `GoogleDocsOpener.exe` automatically copies itself and its icon into `%LOCALAPPDATA%\GoogleDocsOpener` and creates a Windows Start Menu shortcut. Even if you delete the downloaded `.exe` from your `Downloads` folder later, your file associations, login tokens, and settings will never break.

**Supported files:**
- **Google Docs:** `.docx`, `.doc`, `.odt`, `.rtf`
- **Google Sheets:** `.xlsx`, `.xls`, `.ods`, `.csv`, `.tsv`
- **Google Slides:** `.pptx`, `.ppt`, `.odp`

---

## Step 1: Download & Launch `GoogleDocsOpener.exe`

### Option A: Use the Standalone `.exe` (No Python required!)
1. Download **`GoogleDocsOpener.exe`** from the repository (or the Releases page).
2. Double-click **`GoogleDocsOpener.exe`** to open the **Google Docs Opener Control Center**.
   *(Don't worry about leaving `GoogleDocsOpener.exe` in your `Downloads` folder—Step 3 will automatically install a permanent copy into `%LOCALAPPDATA%\GoogleDocsOpener` and add it to your Start Menu!)*

### Option B: Build the `.exe` from Source Yourself (Optional)
If you don't trust pre-built `.exe` files from the internet and want to compile it yourself:
1. Install Python from [python.org/downloads](https://www.python.org/downloads/) (make sure to check **"Add python.exe to PATH"** on the first screen).
2. Download or `git clone` this repository (`opener.py`, `build.bat`, and `icon.ico`).
3. Double-click **`build.bat`**. It will automatically install the required libraries (`google-api-python-client`, `pyinstaller`, etc.), compile **`GoogleDocsOpener.exe`** with the custom icon and DLLs, run a self-test, and clean up temporary build files.

---

## Step 2: Get your `credentials.json` file from Google

**Why isn't this file pre-installed inside the app?**
Google strictly forbids putting API key files on public GitHub repositories—their scanners automatically detect and disable them within minutes. Because of that, you have to generate your own personal `credentials.json` file.

It is **100% free**, does **not** require a credit card, and takes about 2–3 minutes. You only ever have to do this once.

### 1. Create a Google Cloud project
1. Open `GoogleDocsOpener.exe` and click **`1. Open Google Cloud Console`** (or go to [console.cloud.google.com](https://console.cloud.google.com/)) and sign in with your normal Google account.
2. Look at the top-left corner (next to the Google Cloud logo) and click the **Select a project** dropdown button.
3. In the pop-up window, click **New Project** in the top-right corner.
4. Name it something like `DocsOpener` and click **Create**.
5. Wait a few seconds for the notification in the top-right, then click **Select Project** so you are actually inside your new `DocsOpener` project.

### 2. Turn on the Google Drive API
1. In the left sidebar menu (click the `☰` icon in the top-left if it's hidden), go to **APIs & Services** -> **Library**.
2. In the big search bar, type `Google Drive API` and press Enter.
3. Click on **Google Drive API** in the results, then click the blue **Enable** button.

### 3. Configure the Consent Screen & Unlock "Publish App"
Before Google lets you use your key, it makes you fill out a basic "app name" screen.

1. In the left sidebar, click **Google Auth Platform** (or **OAuth consent screen**) -> **Overview**.
2. Click **Get Started** and fill in the basics:
   - **App Information:** Type `DocsOpener` as the App name, select your own Gmail address in the User support email dropdown, and click **Next**.
   - **Audience:** Select **External** and click **Next**.
   - **Contact Information:** Type your own Gmail address again and click **Next**.
   - Check the box to agree to the policy and click **Create**.
3. **Unlock the "Publish App" button (Stops your login from expiring every 7 days):**
   If you go to the **Audience** tab right now, the **Publish app** button will be greyed out asking for a Homepage and Privacy Policy URL. You do **not** need to own a website to pass this—Google doesn't verify the URL unless you apply for commercial brand verification:
   - Click **Branding** in the left sidebar.
   - Scroll down to **App domain**.
   - Paste `https://example.com` (or the URL to this GitHub repo) into **both** the **Application home page** box and the **Application privacy policy link** box.
   - Right below that under **Authorized domains**, click **+ Add domain** and type `example.com` (or `github.com` if you used the repo link).
   - Click **Save** at the bottom.
   - Now click **Audience** in the left sidebar, click the unlocked **Publish app** button, and click **Confirm**.

*(Note: If you prefer not to publish it, you can leave the app in "Testing" mode! Just scroll down on the **Audience** page to **Test users**, click **+ Add users**, and add your own Gmail address. The only difference in Testing mode is that Google will ask you to re-authenticate in the browser once a week).*

### 4. Download & Import `credentials.json`
1. In the left sidebar, go to **Clients** (or **APIs & Services** -> **Credentials**).
2. At the top, click **+ Create Client** (or **+ Create Credentials** -> **OAuth client ID**).
3. For **Application type**, select **Desktop app** *(Important: do NOT choose Web application!)*.
4. Leave the name as `Desktop client 1` and click **Create**.
5. In the pop-up window, click the **Download JSON** button (`⬇`).
6. **You don't even need to rename or move the file!** Just switch back to the **Google Docs Opener Control Center** window and click **`2. Auto-Scan Downloads`** under Step 1.
   - The app will automatically find the `client_secret_...json` file in your `Downloads` folder, verify that it's a valid Desktop OAuth key, and import it safely into `%LOCALAPPDATA%\GoogleDocsOpener\credentials.json`. *(Alternatively, click **`3. Browse JSON File...`** to pick it manually).*

---

## Step 3: Sign In & 1-Click Install Inside the Control Center

Still inside the **Google Docs Opener Control Center** (`1. Setup & Install` tab):

### 1. Connect Your Google Drive Account (Step 2 in the app)
1. Click **`Sign In / Verify Connection`**.
2. A browser window will pop up asking you to choose your Google account.
3. **You will see a warning screen that says "Google hasn't verified this app."**
   - Do not panic—Google shows this because *you* just created the `DocsOpener` app in Step 2 and haven't submitted it as a commercial company.
   - Click the small **Advanced** link at the bottom-left.
   - Click **Go to DocsOpener (unsafe)** at the bottom.
   - Click **Continue** to allow it to upload files to your Drive.
4. Once completed, the badge in the Control Center will turn green (`CONNECTED`).

### 2. Run 1-Click Safe Install & File Registration (Step 3 in the app)
1. Under Step 3 in the Control Center, click **`1-Click Safe Install & Register Files`**.
   - This automatically copies `GoogleDocsOpener.exe` and `icon.ico` into `%LOCALAPPDATA%\GoogleDocsOpener`, creates a **`Google Docs Opener`** shortcut in your Windows Start Menu, and registers all supported Office/OpenDocument file extensions in your Windows Registry.
2. **Final Windows 10/11 "Always Open With" Confirmation:**
   Because Windows 10 and 11 protect default file associations from being silently hijacked without user consent:
   - Right-click any **`.docx`** file on your PC -> **Open with** -> **Choose another app**.
   - Select **Google Docs Opener** from the list and click **Always**.
   - Do this once for an **`.xlsx`** file and once for a **`.pptx`** file.

You're done! Double-clicking any Word, Excel, or PowerPoint file anywhere on your computer will now show a sleek dark-mode upload progress bar and open the converted document straight in your browser.

---

## Features & Customizing (Control Center GUI)

To change settings later, open **Google Docs Opener** from your **Windows Start Menu** (or run `GoogleDocsOpener.exe` with no files).

### 1. Recent Cloud Documents (`2. Recent Documents` tab)
- View a searchable history of every document, spreadsheet, and presentation you've uploaded.
- **Double-click** any row (or click **Open in Browser**) to immediately reopen that Google Doc, or click **Copy Link** to grab the URL for sharing.
- Remove individual cached entries or clear the entire SHA-256 upload cache if you want to force a fresh upload.

### 2. Preferences & Folder Watcher (`3. Settings & Watcher` tab)
- **Preferred Browser:** Choose between `firefox`, `chrome`, `edge`, `brave`, `vivaldi`, or `default` (your default Windows browser).
- **Dedicated Google Drive Folder:** By default, uploaded files are neatly organized inside a folder named **`Google Docs Opener Uploads`** in your Google Drive so your root Drive stays clean. You can customize this folder name or uncheck the box to upload to your Drive root.
- **Auto-Delete Local Files:** Optionally check *"Delete local Office file from PC after uploading to Google Drive"* if you want local `.docx`/`.xlsx`/`.pptx` files removed automatically once they are safely in the cloud.
- **Background Folder Watcher & Windows Autostart:** Pick any local folder (e.g., `Downloads` or a dedicated `ToCloud` folder) and enable the **Folder Watcher**. Whenever a new Office file finishes downloading or is dropped into that folder, it will automatically upload and open in Google Docs in the background!

### 3. Live Diagnostics (`4. Diagnostics & Log` tab)
- View `python_log.txt` live inside the app, open it in Notepad, or clear the log with one click.

---

## Common Mistakes & Troubleshooting

If something isn't working, open **Google Docs Opener** from your Start Menu and check the **`4. Diagnostics & Log`** tab (or **`5. Setup Guide & FAQ`** tab).

### 1. App says: *"MISTAKE DETECTED: You selected 'Web application' instead of 'Desktop app'"*
When creating your OAuth Client ID in Google Cloud Console, you picked **Web application** or **Service Account** instead of **Desktop app**.
- **Fix:** Go back to Google Cloud Console -> **Clients** (or **Credentials**) -> **+ Create Client** -> choose **Desktop app** -> download the new JSON file, and click **`2. Auto-Scan Downloads`** in the app.

### 2. Google gives `Error 403: access_denied` in the browser when trying to log in
This happens when your Google Cloud project is still in "Testing" mode and doesn't know who is allowed to log in.
- **Fix:** Go back to the [Google Cloud Console](https://console.cloud.google.com/) -> **OAuth consent screen / Audience**. Either click **Publish App** (recommended), or scroll down to **Test users**, click **+ Add Users**, and type in your own Gmail address.

### 3. It worked for a week, and then asked me to log in again (or failed to refresh token)
You skipped Step 2.3 and left your Google Cloud app in "Testing" mode, which forces `token.json` to expire every 7 days.
- **Fix:** Go to the Google Cloud Console -> **OAuth consent screen / Audience** -> click **Publish App** (see Step 2.3 above for how to unlock the button using `https://example.com`). Then open the Control Center and click **`Sign In / Verify Connection`** one last time.

### 4. I edited my file in Google Docs, emailed the `.docx` file from my PC to someone, and it was still the old blank version!
Remember how this tool works: it uploads and converts the file *into* Google Docs. Once it's in Google Docs, your browser doesn't reach back onto your hard drive to overwrite the original `.docx` file.
- **Fix:** Whenever you finish editing a document in Google Docs and need to send it to someone as a `.docx` or `.pdf`, click **File** -> **Download** inside the Google Docs tab in your browser.

### 5. Where are my credentials, cache, and logs saved? Can I delete the `.exe` from my `Downloads` folder?
Yes! Once you click **`1-Click Safe Install & Register Files`** in the Control Center, everything is stored inside:
`%LOCALAPPDATA%\GoogleDocsOpener` *(you can click **`Open AppData Folder`** in the Control Center to jump straight there)*.
You can safely delete the originally downloaded `GoogleDocsOpener.exe` and `client_secret_...json` from your `Downloads` folder, and launch the Control Center anytime from your Windows Start Menu.

### 6. Can I send `GoogleDocsOpener.exe` and my `credentials.json` to a friend so they don't have to do Step 2?
Yes, with two rules:
1. **Only send `GoogleDocsOpener.exe` and `credentials.json`—NEVER send `token.json` or `upload_cache.json`!** `token.json` (stored in your `%LOCALAPPDATA%\GoogleDocsOpener` folder) is your personal Google account login session. If you send someone your `token.json`, all of *their* documents will upload into *your* personal Google Drive.
2. You **can** share your `credentials.json` privately with a friend so they can skip the Google Cloud setup and just sign in with their own Google account. Just never upload `credentials.json` to a public website or public GitHub repository.
