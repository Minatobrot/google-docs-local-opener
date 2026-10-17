# Local Office to Google Docs Opener (Windows)

I wanted a way to double-click local Word, Excel, and PowerPoint files on Windows and have them automatically upload to Google Drive and open in Google Docs inside my browser (Firefox)—without paying for Microsoft Office or manually dragging files into a browser tab every time.

> **Note / Disclaimer:** The code and documentation in this repo are mainly AI-generated. I didn't write the Python code myself—I just had the idea for the workflow, tested the scripts when they crashed (like fixing Windows batch issues and German umlaut `ä/ö/ü` encoding bugs), and came up with the logic for how it should behave (like using SHA-256 file hashing so moving files around doesn't cause duplicate uploads).

---

## How it actually works (Read this first)

Before setting this up, there are three things you should know about how this handles your files:

1. **It won't spam your Google Drive with duplicates:** The first time you double-click a file, it uploads it and saves a digital fingerprint (`SHA-256` hash) of that file's contents in `upload_cache.json`. Next time you double-click that file, it skips the upload and immediately opens the existing Google Doc link.
2. **Moving or renaming local files doesn't break the link:** Because it checks the actual contents of the file instead of the folder path, you can move a `.docx` from your `Downloads` folder to your `Desktop` or rename it, and double-clicking it will still open the same Google Doc. (And if you delete the file inside Google Drive later, the script notices it's in the trash and automatically uploads a fresh copy next time you click it.)
3. **Your edits happen in the cloud, NOT on the local file:** Once a file is uploaded, the local `.docx` on your hard drive basically acts like a shortcut to your Google Doc. When you type and make changes inside Google Docs in your browser, those changes are saved in your Google Drive—they are **not** synced back into the old `.docx` file sitting on your PC. If you need to email someone an updated `.docx` later, download it from inside Google Docs (`File` -> `Download` -> `Microsoft Word`).

**Supported files:** `.docx`, `.doc`, `.odt` (Google Docs) | `.xlsx`, `.xls`, `.ods` (Google Sheets) | `.pptx`, `.ppt`, `.odp` (Google Slides)

---

## Step 1: Download the files & install Python

### 1. Put this folder somewhere permanent
If you are downloading this from GitHub, click the green **`<> Code`** button at the top of the page and click **Download ZIP** (or `git clone` it if you use Git).

Extract the folder and put it somewhere you won't accidentally delete it—for example, `C:\GoogleDocsOpener` or inside your `Documents` folder. **Do not** leave the script inside your `Downloads` folder where you might clean it out later, because Windows will link your Office files to this folder's location.

### 2. Install Python
If you don't already have Python installed on Windows:
1. Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest version for Windows.
2. Run the installer. **STOP on the very first screen** and check the box at the bottom that says **"Add python.exe to PATH"**. If you miss this box, Windows won't know where Python is.
3. Click **Install Now**.

### 3. Run `setup.bat`
Open the folder where you extracted these files and double-click **`setup.bat`**. 
A black command window will open and install the official Google Drive libraries (`google-api-python-client`, etc.). Wait until it says `Setup Complete!` and press any key to close it.

---

## Step 2: Get your `credentials.json` file from Google

**Why isn't this file included in the download?**
Google strictly forbids putting API key files on public GitHub repositories—their scanners automatically detect and disable them within minutes. Because of that, you have to generate your own personal `credentials.json` file. 

It is **100% free**, does **not** require a credit card, and takes about 3 minutes. You only ever have to do this once.

### 1. Create a Google Cloud project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and sign in with your normal Google account. *(If it's your first time there, accept the terms of service).*
2. Look at the top-left corner (next to the Google Cloud logo) and click the **Select a project** dropdown button (it might already have a random project name selected).
3. In the pop-up window, click **New Project** in the top-right corner.
4. Name it something like `DocsOpener` and click **Create**.
5. Wait a few seconds for the notification in the top-right, then click **Select Project** so you are actually inside your new `DocsOpener` project.

### 2. Turn on the Google Drive API
1. In the left sidebar menu (click the `☰` icon in the top-left if it's hidden), go to **APIs & Services** -> **Library**.
2. In the big search bar, type `Google Drive API` and press Enter.
3. Click on **Google Drive API** in the results, then click the blue **Enable** button.

### 3. Configure the Consent Screen (Don't skip this!)
Before Google lets you download a key, it makes you fill out a basic "app name" screen.
1. In the left sidebar, click **OAuth consent screen** under **APIs & Services** (Google sometimes renames this tab to **Google Auth Platform** -> **Overview**).
2. If you see a **Get Started** button:
   - **App Information:** Type `DocsOpener` as the App name, select your own Gmail address in the User support email dropdown, and click **Next**.
   - **Audience:** Select **External** and click **Next**.
   - **Contact Information:** Type your own Gmail address again and click **Next**.
   - Check the box to agree to the policy and click **Create**.
   *(If you have the older Google interface instead: select **External**, click **Create**, fill in the App name and the two email boxes, and click **Save and Continue** through all 4 steps).*
3. **CRITICAL STEP (Stop your login from expiring every 7 days):**
   - Look in the left sidebar under **Audience** (or right on the main **OAuth consent screen** page).
   - Under "Publishing status", it currently says *Testing*. Click the **Publish App** button and click **Confirm**.
   - *Why?* If you leave it in "Testing", Google forces you to log in again every 7 days. Because this script only asks for basic permission to manage files it uploads itself (`drive.file`), Google does **not** require any app review when you click Publish. Your login will now last permanently.

### 4. Download `credentials.json`
1. In the left sidebar, go to **Clients** (or **APIs & Services** -> **Credentials**).
2. At the top, click **+ Create Client** (or **+ Create Credentials** -> **OAuth client ID**).
3. For **Application type**, select **Desktop app**.
4. Leave the name as `Desktop client 1` and click **Create**.
5. A window will pop up with a button that says **Download JSON** (an icon with a downward arrow `⬇`). Click it to download the file.
6. Find that downloaded file (it will have a long ugly name like `client_secret_123456...json`).
7. Rename it to **`credentials.json`** and move it into the folder right next to `opener.py` and `run_docs.bat`.

---

## Step 3: First run & Google login

Before making this your default Windows app, test it once to log into your Google account:

1. Find any `.docx` file on your computer.
2. **Drag and drop** that `.docx` file directly on top of **`run_docs.bat`** inside your folder.
3. A browser window will pop up asking you to choose your Google account.
4. **You will see a warning screen that says "Google hasn't verified this app."** 
   - Do not panic—Google shows this because *you* just created the `DocsOpener` app in Step 2 and haven't submitted it as a commercial company.
   - Click the small **Advanced** link at the bottom-left.
   - Click **Go to DocsOpener (unsafe)** at the bottom.
   - Click **Continue** to allow it to upload files to your Drive.
5. Once you click Continue, the browser tab will say *"The authentication flow has completed."* A few seconds later, your `.docx` file will automatically pop open in Firefox inside Google Docs!

*(You'll notice a new `token.json` file appeared in your folder. That stores your login session so you never have to do that login step again).*

---

## Step 4: Override Word, Excel, and PowerPoint on Windows

Now that it works, tell Windows to use `run_docs.bat` whenever you double-click an Office file:

1. Right-click any **`.docx`** file on your PC.
2. Hover over **Open with** and click **Choose another app**.
3. Scroll all the way to the bottom and click **Choose an app on your PC**.
4. Browse to your script folder, click on **`run_docs.bat`**, and click **Open**.
5. Click the **`Always`** button at the bottom of the window.
6. Do this exact same right-click -> **Open with** -> **Always** step once for an **`.xlsx`** (Excel) file and once for a **`.pptx`** (PowerPoint) file.

You're done. Double-clicking any Word, Excel, or PowerPoint file anywhere on your computer will now send it straight to Google Docs in your browser.

---

## Customizing (Use Chrome/Edge, or Auto-Delete Local Files)

If you open **`opener.py`** in Notepad, you'll see two settings right at the very top of the file:

```python
PREFERRED_BROWSER = "firefox"
DELETE_LOCAL_AFTER_UPLOAD = False
```

- **To use Chrome, Edge, or your default Windows browser instead of Firefox:** Change `"firefox"` to `"chrome"`, `"edge"`, or `"default"` and save the file.
- **To automatically delete the local `.docx` file after it uploads:** Change `False` to `True` (make sure the `T` is capitalized). Be careful with this: if you set it to `True`, the local file is deleted from your PC as soon as the upload finishes.

---

## Common Mistakes & Troubleshooting

If something isn't working or a black box flashes and disappears, **open `python_log.txt` inside the script folder**. The script writes down every single step it takes and the exact reason it crashed inside that file.

### 1. `python_log.txt` says: `ERROR: 'credentials.json' not found` (even though it's in the folder!)
Windows hides file extensions by default. When you renamed the downloaded file to `credentials.json`, Windows secretly named it **`credentials.json.json`**.
- **Fix:** At the top of Windows File Explorer, click **View** -> **Show** -> check **File name extensions** (on Windows 10, click the **View** tab and check **File name extensions**). Look at the file again and delete the extra `.json` at the end. *(Note: the current version of `opener.py` actually checks for `credentials.json.json` automatically to save you the headache, but it's still good to check).*

### 2. Google gives `Error 403: access_denied` in the browser when trying to log in
This happens when your Google Cloud project is still in "Testing" mode and doesn't know who is allowed to log in.
- **Fix:** Go back to the [Google Cloud Console](https://console.cloud.google.com/) -> **OAuth consent screen** (or **Audience**). Either click **Publish App** (recommended), or scroll down to **Test users**, click **+ Add Users**, and type in your own Gmail address.

### 3. It worked for a week, and then asked me to log in again (or crashed with `invalid_grant`)
You skipped Step 2.3 and left your Google Cloud app in "Testing" mode, which forces `token.json` to self-destruct every 7 days.
- **Fix:** Go to the Google Cloud Console -> **OAuth consent screen / Audience** -> click **Publish App**. Then delete the old `token.json` file inside your script folder and double-click a `.docx` file to log in one last time.

### 4. I edited my file in Google Docs, emailed the `.docx` file from my PC to someone, and it was still the old blank version!
Remember how this tool works: it uploads the file *into* Google Docs. Once it's in Google Docs, your browser doesn't reach back onto your hard drive to overwrite the original `.docx` file. 
- **Fix:** Whenever you finish editing a document in Google Docs and need to send it to someone as a `.docx` or `.pdf`, click **File** -> **Download** inside the Google Docs tab in your browser.

### 5. Dragging a file onto `run_docs.bat` works, but setting it as the Default App via "Open With" fails on Windows 11
Some builds of Windows 11 refuse to pass file paths to `.bat` files when assigned through the right-click menu. If that happens on your PC, you can turn `opener.py` into a real `.exe` program in 10 seconds:
1. Open Command Prompt inside your script folder (click the address bar in File Explorer, type `cmd`, and press Enter).
2. Run: `py -m pip install pyinstaller`
3. Run: `py -m PyInstaller --onefile --distpath . opener.py`
4. A real **`opener.exe`** file will appear right inside your folder. Right-click your `.docx` file -> **Open with** -> **Choose another app** -> pick **`opener.exe`** instead of `run_docs.bat`.

### 6. Can I zip up my folder and send it to a friend so they don't have to do Step 2?
Yes, with two rules:
1. **DELETE `token.json` and `upload_cache.json` from the zip before sending it!** `token.json` is your personal Google account login session. If you send someone your `token.json`, all of *their* documents will upload into *your* personal Google Drive.
2. You **can** leave your `credentials.json` inside a private `.zip` for a friend so they can skip the whole Google Cloud setup. Just never upload `credentials.json` to a public website or public GitHub page.
