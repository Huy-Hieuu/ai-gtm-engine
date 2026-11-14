# AI Speed-to-Lead Agent for GoHighLevel

New lead in GoHighLevel → AI texts back in under 60 seconds → qualifies the lead by SMS → books a meeting → moves the pipeline stage.

Python + FastAPI. GoHighLevel (GHL) owns the contacts, messages, calendars and pipeline; this app only listens, decides and calls the [GHL API v2](https://marketplace.gohighlevel.com/docs/). Built step by step; each step's section below is a guide you can run yourself.

## Layout

| Path | What it is |
|------|------------|
| `app/config.py` | The only place that reads environment variables (`.env`). |
| `app/ghl.py` | The only GHL API client: one small function per endpoint. |
| `app/db.py` | The only owner of the SQLite schema and queries (`events`, `installs`). |
| `app/webhooks.py` | Webhook signature check and one handler per event type. |
| `app/main.py` | FastAPI app (`GET /health`, `POST /webhook`, `GET /oauth/install`, `GET /oauth/callback`). |
| `app/hello.py` | Step 1 CLI. |
| `render.yaml` | Render free web service (Step 2). |
| `tests/` | Tests against JSON fixtures from the docs; no test calls the real API. |

## Step 1 - Hello API

Goal: prove the token works by listing contacts and sending one SMS.

### 1. Create a Private Integration Token

In your GHL sandbox sub-account:

1. **Settings → Private Integrations → Create new Integration.** (Not in settings? Enable it under Labs.)
2. Name it, e.g. `speed-to-lead`.
3. Select only these scopes:

   | Scope | Used by |
   |-------|---------|
   | `contacts.readonly` | `POST /contacts/search` (list contacts) |
   | `conversations/message.write` | `POST /conversations/messages` (send SMS) |

4. Copy the token. GHL shows it only once.

Your location (sub-account) id is the part after `/location/` in the GHL app URL.

### 2. Fill `.env`

```bash
cp .env.example .env
```

Set `GHL_PRIVATE_TOKEN` and `GHL_LOCATION_ID`. `.env` is git-ignored; never commit it.

### 3. Install (Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Expected: `28 passed`.

### 4. List contacts

```bash
python -m app.hello contacts
```

Expected: up to 10 lines of `id<TAB>name<TAB>phone`, for example

```
102goXVW3lIExEQPOnd3	john doe	+123456789
```

It worked if the names match the contacts in **Contacts** in GHL. Add a test contact with your own mobile number if the list is empty.

### 5. Send one SMS

Copy the id of a contact whose phone you own:

```bash
python -m app.hello sms 102goXVW3lIExEQPOnd3 "Hello from the API"
```

Expected: one line with the message id, for example `t22c6DQcTDf3MjRhwf77`.

It worked if the message shows in that contact's **Conversations** tab in GHL (and arrives on the phone, if the sub-account has an SMS number).

### If it fails

| Output | Fix |
|--------|-----|
| `error: GHL_PRIVATE_TOKEN is not set` / `GHL_LOCATION_ID is not set` | Fill `.env` (step 2). |
| `error: GHL rejected the token (401 ...)` | Token copied wrong, rotated, or missing the scope for that call. |
| `error: GHL /conversations/messages failed (400 ...)` | Read GHL's message; usually the contact has no phone or the sub-account has no SMS number. |

All GHL calls send `Authorization: Bearer <token>` and the `Version` header from the endpoint docs (`app/ghl.py`, `API_VERSION`).

## Step 2 - Webhooks

Goal: GHL tells the app about every new contact (`ContactCreate`) and every reply (`InboundMessage`) at a public URL. The app checks the request really comes from GHL, logs it, and skips resends.

How `POST /webhook` works:

1. Checks the `X-GHL-Signature` header: an Ed25519 signature of the raw body, verified with GHL's public key from the [Webhook Integration Guide](https://marketplace.gohighlevel.com/docs/webhook/WebhookIntegrationGuide) (`app/config.py`, `GHL_WEBHOOK_PUBLIC_KEY`). Missing or wrong → `401`. The legacy RSA `X-WH-Signature` header was retired on 2026-09-01 and is not accepted.
2. Event types without a handler → `200 {"status": "ignored"}`, nothing stored.
3. Stores the event in the SQLite `events` table keyed by GHL's `webhookId`. Already there → `200 {"status": "duplicate"}` and no second handling. GHL retries any non-2xx or timed-out delivery up to 12 times, so the same event can arrive more than once.
4. Answers `200 {"status": "accepted"}` at once, then runs the handler for that type after the response and sets `handled = 1`. For now the handlers only log.

### 1. Run locally (optional)

```bash
uvicorn app.main:app --reload
```

In a second terminal:

```bash
curl -i -X POST localhost:8000/webhook -H 'Content-Type: application/json' -d '{"type":"ContactCreate","webhookId":"test-123"}'
```

Expected: `HTTP/1.1 401 Unauthorized` and `{"detail":"invalid signature"}`, and in the server log

```
WARNING app.main: webhook rejected: bad or missing X-GHL-Signature
```

Only GHL holds the private key, so any hand-made request is rejected; that is the point. The signed path is covered by `pytest -q`, which signs fixtures with a test key.

### 2. Deploy to Render

1. [Render dashboard](https://dashboard.render.com) → **New → Blueprint** → connect this GitHub repo. Render reads `render.yaml` and creates one free web service named `ghl-speed-to-lead`.
2. It asks for `GHL_PRIVATE_TOKEN` and `GHL_LOCATION_ID`; paste the Step 1 values (Step 2 itself does not use them).
3. **Apply**. Wait for the deploy log to end with `Your service is live`.

Check, with your service URL:

```bash
curl https://<app>.onrender.com/health
```

Expected: `{"status":"ok"}`.

Free tier limits that matter here:

- **The disk is temporary.** The SQLite file starts empty after every deploy or restart, so the duplicate memory resets too. Fine for the demo; a paid disk or Postgres fixes it later.
- **The service sleeps after 15 minutes without traffic** and takes up to about a minute to wake. GHL retries deliveries that time out, so the first webhook after a sleep arrives late rather than getting lost.

### 3. Point GHL webhooks at the app

GHL sends these webhooks to Marketplace apps installed in a sub-account; a Private Integration token does not receive them. Create a private app for your sandbox once:

1. [marketplace.gohighlevel.com](https://marketplace.gohighlevel.com) → **My Apps → Create App**. Distribution: **Sub-account**, private.
2. **Advanced Settings → Auth**:

   | Scope | Needed for webhook |
   |-------|--------------------|
   | `contacts.readonly` | `ContactCreate` |
   | `conversations/message.readonly` | `InboundMessage` |

   Redirect URL: `https://<app>.onrender.com/oauth/callback` (the route arrives in Step 5; until then GHL lands on a 404 page, but the code it needs is in the address bar). Create a client id and secret and keep them out of git.
3. **Advanced Settings → Webhooks**: turn on **ContactCreate** and **InboundMessage** and paste `https://<app>.onrender.com/webhook` for both.
4. Install the app in the sandbox: open this URL (fill in the client id), pick the sandbox sub-account, and copy the `code` value from the address bar you land on.

   ```
   https://marketplace.gohighlevel.com/v2/oauth/chooselocation?response_type=code&redirect_uri=https://<app>.onrender.com/oauth/callback&client_id=<CLIENT_ID>&scope=contacts.readonly conversations/message.readonly
   ```

5. Finish the install by trading that code for a token once (per the [OAuth FAQ](https://marketplace.gohighlevel.com/docs/oauth/Faqs), events start after this; Step 5 automates it):

   ```bash
   curl -X POST https://services.leadconnectorhq.com/oauth/token \
     -d client_id=<CLIENT_ID> -d client_secret=<CLIENT_SECRET> \
     -d grant_type=authorization_code -d code=<CODE> -d user_type=Location \
     -d redirect_uri=https://<app>.onrender.com/oauth/callback
   ```

   Expected: JSON with `access_token` and your `locationId`. Nothing needs to be saved yet.

### 4. Create a test contact

In the sandbox: **Contacts → Add Contact**, e.g. `Jane Test` with your mobile number. Within seconds Render → your service → **Logs** shows

```
INFO app.main: webhook received id=<webhookId> type=ContactCreate
INFO app.webhooks: ContactCreate contact=<contactId> name='Jane Test' location=<locationId>
```

It worked if `<contactId>` matches the id in the contact's URL in GHL. Text the sandbox's SMS number from that phone and you get an `InboundMessage contact=... channel=SMS body='...'` line the same way.

GHL's own view: marketplace → your app → **Insights → Logs → Webhooks** lists each delivery with status `200`.

### 5. Tell a duplicate was skipped

When GHL delivers the same `webhookId` twice, the second delivery logs

```
INFO app.main: webhook duplicate skipped id=<webhookId> type=ContactCreate
```

and no second `ContactCreate contact=...` line follows. GHL only resends after a failed or timed-out delivery, so you cannot force one from the dashboard; the test sends the same signed event twice and checks the handler ran once:

```bash
pytest -q tests/test_webhook.py -k duplicate
```

Expected: `2 passed`.

### If it fails

| What you see | Fix |
|--------------|-----|
| No log line at all after creating a contact | App not installed in this sub-account (3.4-3.5), event not turned on (3.3), or scope missing (3.2). The Webhook Logs page in 3 shows whether GHL tried. |
| `webhook rejected: bad or missing X-GHL-Signature` on GHL deliveries | Something between GHL and the app changed the body, or GHL rotated its key: compare `GHL_WEBHOOK_PUBLIC_KEY` in `app/config.py` with the docs. |
| `webhook ignored id=... type=ContactUpdate` | An event without a handler is turned on in 3.3. Harmless; turn it off. |
| `webhook failed id=...` with a traceback | A handler crashed; the event stays `handled = 0` and the traceback says why. |

## Step 5 - OAuth app

Goal: a client installs the app into their sub-account with one click, and the app keeps a working token for it without anyone copying tokens around. The Private Integration Token from Step 1 stays as the fallback for a location without an install.

How it works:

1. `GET /oauth/install` redirects to GHL's consent page (`https://marketplace.gohighlevel.com/v2/oauth/chooselocation`) with your client id, redirect URL and the scopes in `app/ghl.py` (`SCOPES`).
2. The admin picks a sub-account and approves. GHL sends the browser to `GET /oauth/callback?code=...`.
3. The app swaps the code at `POST /oauth/token` for an access token (lives ~24 h) and a refresh token, and saves both in the `installs` table, keyed by location id and encrypted with `APP_ENCRYPTION_KEY` (Fernet).
4. Every GHL call asks `get_access_token(location_id)` in `app/ghl.py` for its token: the install's access token, refreshed first if it expires within 5 minutes; with no install, `GHL_PRIVATE_TOKEN`. If GHL still answers `401`, the app refreshes once and retries once; a second `401` is an error. A refresh token works once, so each refresh saves the new pair.

### 1. Set up the app in the developer portal

Reuse the app from Step 2 (or create one: [marketplace.gohighlevel.com](https://marketplace.gohighlevel.com) → **My Apps → Create App**, Distribution **Sub-account**, private).

**Advanced Settings → Auth**:

1. Scopes: select all of these (each endpoint's doc page names its scope):

   | Scope | Used by |
   |-------|---------|
   | `contacts.readonly` | list contacts, `ContactCreate` webhook |
   | `conversations/message.readonly` | `InboundMessage` webhook |
   | `conversations/message.write` | send SMS |
   | `calendars.readonly` | free slots (Step 4) |
   | `calendars/events.write` | book the appointment (Step 4) |
   | `opportunities.readonly` | read pipelines (Step 4) |
   | `opportunities.write` | move the stage to Booked (Step 4) |

2. Redirect URL: `https://<app>.onrender.com/oauth/callback` → **Add**.
3. Client Keys → **Add**. Copy the client id and the client secret; GHL shows the secret only once.

### 2. Generate the encryption key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Expected: one 44-character line ending in `=`. Keep it next to the client secret; tokens saved with one key cannot be read with another.

### 3. Set the env vars on Render

Render → your service → **Environment**, add:

| Key | Value |
|-----|-------|
| `GHL_CLIENT_ID` | client id from 1.3 |
| `GHL_CLIENT_SECRET` | client secret from 1.3 |
| `GHL_REDIRECT_URI` | `https://<app>.onrender.com/oauth/callback`, exactly as in 1.2 |
| `APP_ENCRYPTION_KEY` | the key from 2 |

**Save, rebuild and deploy**, and wait for `Your service is live`. For a local run, put the same names in `.env` (see `.env.example`); never commit them.

### 4. Install into the sandbox

Open `https://<app>.onrender.com/oauth/install` in a browser where you are logged in to GHL. GHL asks you to pick a sub-account: choose the sandbox, check the listed permissions match the scope table, and confirm.

Expected: the browser lands on `/oauth/callback` and shows

```json
{"status":"installed","locationId":"<your sandbox location id>"}
```

and Render → **Logs** shows

```
INFO app.main: oauth installed location=<locationId>
```

It worked if that location id is the one in your sandbox's GHL URL after `/location/`.

The refresh and retry rules have no button to press (a token takes a day to expire); the tests drive them against the token endpoint's documented response:

```bash
pytest -q tests/test_oauth.py
```

Expected: `8 passed`.

On the free tier the database resets on every deploy or restart (Step 2), so the install is gone after one: open `/oauth/install` again. Until then calls fall back to `GHL_PRIVATE_TOKEN`.

### If it fails

| What you see | Fix |
|--------------|-----|
| `/oauth/install` → `GHL_CLIENT_ID and GHL_REDIRECT_URI must be set` | Env vars from 3 missing on this service, or not deployed yet. |
| GHL consent page shows an error about the redirect URI | `GHL_REDIRECT_URI` differs from the Redirect URL saved in 1.2; they must match character for character. |
| Callback → `502` with `GHL /oauth/token failed (400 ...)` | The code is single-use and short-lived: start again from `/oauth/install` instead of reloading the callback. Also check the client secret. |
| Callback → `500 Internal Server Error`, log says `APP_ENCRYPTION_KEY is not set` or `Fernet key must be 32 url-safe base64-encoded bytes` | Set `APP_ENCRYPTION_KEY` to the exact line from 2. |
| `GHL rejected the token (401 ...)` after a refresh | The install was removed in GHL or the scope is missing: reinstall from `/oauth/install`. |
