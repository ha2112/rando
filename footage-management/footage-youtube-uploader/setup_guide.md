# SETUP_GUIDE.md

## Google Cloud & YouTube Data API v3 — Setup Guide

This guide takes you from zero to a working `client_secrets.json` in ~10 minutes.

---

## Prerequisites

- A Google account that **owns or manages** the YouTube channel you want to upload to.
- Python 3.10+

---

## Step 1 — Create a Google Cloud Project

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com).
2. Click the **project selector** dropdown at the top-left → **"New Project"**.
3. Name it (e.g. `dji-footage-uploader`) and click **Create**.
4. Make sure the new project is **selected** in the dropdown before continuing.

---

## Step 2 — Enable the YouTube Data API v3

1. In the left sidebar go to **APIs & Services → Library**.
2. Search for **"YouTube Data API v3"** and click on it.
3. Click **Enable**.

---

## Step 3 — Configure the OAuth Consent Screen

> This is required before you can create OAuth credentials.

1. Go to **APIs & Services → OAuth consent screen**.
2. Choose **External** → **Create**.
3. Fill in the required fields:
   - **App name**: `DJI Footage Uploader` (any name works)
   - **User support email**: your Gmail address
   - **Developer contact information**: same email
4. Click **Save and Continue** through the Scopes screen (no changes needed there).
5. On the **Test users** screen:
   - Click **"+ Add Users"**
   - Add the Gmail address that owns your YouTube channel.
   - Click **Save and Continue**.
6. Click **Back to Dashboard**.

> **Why "Test Users"?**  
> While the app is in _Testing_ mode, only explicitly added test users can log in.  
> Since this is a personal tool you'll never need to publish it — testing mode is fine permanently.

---

## Step 4 — Create OAuth 2.0 Credentials

1. Go to **APIs & Services → Credentials**.
2. Click **"+ Create Credentials"** → **"OAuth client ID"**.
3. Choose **Application type: Desktop app**.
4. Name it (e.g. `uploader-desktop`) → **Create**.
5. In the confirmation dialog click **"Download JSON"**.
6. Rename the downloaded file to exactly **`client_secrets.json`**.
7. Place it **in the same folder as `youtube_uploader.py`**.

---

## Step 5 — Install Python Dependencies

```bash
pip install google-auth-oauthlib google-api-python-client tqdm python-dotenv
```

---

## Step 6 — Configure Your `.env` File

In the same folder as the script, create (or update) a `.env` file:

```
DJI_FOOTAGE_FOLDER_PATH=/Users/yourname/path/to/footage
```

---

## Step 7 — First Run (Browser Authentication)

```bash
python youtube_uploader.py
```

1. A browser tab will open automatically asking you to **sign in with Google**.
2. Choose the account you added as a Test User in Step 3.
3. You'll see a **"Google hasn't verified this app"** warning — click **"Continue"** (this is expected for apps in testing mode).
4. Grant the **"Manage your YouTube account"** permission.
5. The browser will show _"The authentication flow has completed."_ — you can close it.

A `token.json` file is saved locally. **Subsequent runs will not open the browser.**

---

## File Reference

```
your-folder/
├── youtube_uploader.py     ← the script
├── client_secrets.json     ← downloaded from Google Cloud (Step 4)
├── token.json              ← auto-created on first run; contains your credentials
├── uploaded_files.json     ← auto-created; tracks uploaded files (do not delete!)
└── .env                    ← contains DJI_FOOTAGE_FOLDER_PATH
```

---

## Daily Quota Notes

The YouTube Data API v3 gives you **10,000 quota units per day**.  
Each video upload costs **1,600 units**.  
This means you can upload roughly **6 videos per day** on the free tier.

To request a quota increase:

1. Go to **APIs & Services → Quotas** in Google Cloud Console.
2. Search for **"YouTube Data API v3"**.
3. Click the pencil icon next to _"Queries per day"_ and submit an increase request.

When the quota is hit the script will exit gracefully, save its progress,
and resume from where it left off the next time you run it.

---

## Troubleshooting

| Error                                           | Fix                                                             |
| ----------------------------------------------- | --------------------------------------------------------------- |
| `client_secrets.json not found`                 | Make sure the file is in the same folder as the script          |
| `Access blocked: This app's request is invalid` | Ensure your Google account is listed as a Test User (Step 3)    |
| `Token has been expired or revoked`             | Delete `token.json` and run the script again to re-authenticate |
| `quotaExceeded`                                 | Wait until midnight Pacific Time for the quota to reset         |
| `The caller does not have permission`           | You must be signed in as the **channel owner** (or a manager)   |
