# Daily Football Instagram Briefing

A GitHub Actions workflow that runs in the cloud, collects same-day football news from RSS feeds, asks Gemini to select and summarise the most important stories, creates a 2–3 image carousel, and publishes it to an Instagram Business account through Meta's official API.

## Cost target

- GitHub Actions: use a **public repository** so standard GitHub-hosted runner usage is free.
- Gemini API: use an eligible free-tier model and stay within its free quotas.
- RSS: free feeds.
- Pillow/Python: free.
- Instagram publishing API: no separate API subscription fee, but requires a correctly configured Meta developer app and permissions.
- No paid hosting is used. The JPEGs are placed in this public repository so Meta can fetch them directly.

Free-tier quotas and provider terms can change. Do not add a payment method or enable billing if the goal is strictly $0.

## What it does

- Runs daily at approximately **8:37 PM India Standard Time**.
- Collects RSS stories whose published date falls on the current India date.
- Deduplicates stories and asks Gemini to select 2–6 important distinct stories.
- Creates 2 or 3 1080×1350 JPEG slides.
- Commits the generated slides to `public/daily/`.
- Publishes them as one Instagram carousel.
- Skips publishing if there are fewer than two supported stories.

The workflow can also be started manually from the Actions tab.

## 1. Create the repository

1. Create a **new public repository** on GitHub, e.g. `daily-football-instagram`.
2. Upload the contents of this project ZIP to the repository root.
3. Use `main` as the default branch.
4. In the repository, open **Settings → Actions → General** and ensure the workflow has permission to read and write repository contents (the workflow also declares `contents: write`).
5. Open **Actions** and enable workflows if GitHub prompts you.

A public repository means the source code and generated carousel JPEGs are public. Never put API keys or access tokens in files.

## 2. Create a Gemini API key

1. Open https://aistudio.google.com/app/apikey
2. Create an API key if the free tier is available to your account.
3. Do not enable billing or add a card.
4. In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret**.
5. Name it `GEMINI_API_KEY` and paste the key as its value.

The default model is `gemini-2.5-flash-lite`. If Google no longer offers this model on the free tier, update `GEMINI_MODEL` in the workflow to a currently free-tier text model. Do not select a paid model.

## 3. Configure Instagram publishing

This project uses **Instagram API with Instagram Login** and the `graph.instagram.com` host.

Requirements:
- Instagram Business or Creator account.
- A Meta developer app configured for Instagram API with Instagram Login.
- The app must be authorised for the Instagram account being published to.
- Permissions: `instagram_business_basic` and `instagram_business_content_publish`.
- An Instagram User ID and valid access token.

Meta setup starts here:
https://developers.facebook.com/apps/

Follow Meta's current Instagram Login and Content Publishing setup instructions. For a personal-use app publishing only to an account you control, use the access level and testing mode Meta currently allows. Do not attempt to bypass app review, account eligibility, or identity checks.

Add these GitHub Actions repository secrets:

| Secret | Value |
|---|---|
| `IG_ACCESS_TOKEN` | Instagram User access token |
| `IG_USER_ID` | Instagram professional account ID |

Never share these values in chat, commit them, or put them in the workflow file. If a token is exposed, revoke it and create a replacement.

The workflow currently uses Graph API version `v26.0` via `META_GRAPH_VERSION`. If Meta's dashboard/docs show a newer supported version, update this environment value in the workflow.

## 4. Enable the workflow

1. Open the repository's **Actions** tab.
2. Select **Daily Football Instagram Briefing**.
3. Click **Run workflow** for a manual test.
4. Open the run and inspect each step's logs.
5. Confirm the carousel appears on Instagram.
6. The daily schedule then runs automatically at approximately 8:37 PM IST.

GitHub scheduled runs can be delayed during busy periods. Public-repository schedules can be disabled after 60 days without repository activity; check the Actions tab periodically.

## 5. Customise the account name on graphics

In `app.py`, find `@YOURPAGE` and replace it with your Instagram handle (without needing to provide it to anyone).

## Important reliability notes

- RSS publishers can omit timestamps or publish late; stories without a usable same-day timestamp are skipped.
- The AI is instructed to use only supplied source material, and each selected story is tied back to a real RSS item. Review the first few posts for quality.
- The code does not use Google Search grounding, because that can have separate billing conditions. It summarises only the RSS items.
- The generated image URLs are public because Instagram's API must fetch them without signing in.
- The GitHub repository must remain public for this public-asset hosting approach and free standard Actions runner usage.
- This is a starter implementation, not a guarantee of uninterrupted free service. Provider quotas, API access, token expiry, and platform policies can change.

## Troubleshooting

- `Missing GEMINI_API_KEY`: add the repository secret with that exact name.
- `Missing IG_ACCESS_TOKEN` or `IG_USER_ID`: add both Instagram secrets.
- Meta permission error: recheck Instagram Login configuration and publishing permissions.
- Image URL error: verify the repository is public and the generated JPEGs exist under `public/daily/`.
- Gemini 429: the free-tier request limit may have been reached; retry on the next day.
- No post generated: inspect the feed collection and AI selection logs; the workflow deliberately skips days with insufficient same-day stories.
