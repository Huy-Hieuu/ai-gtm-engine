"""Settings. The only module that reads environment variables (and `.env`)."""

import os

from dotenv import load_dotenv

load_dotenv()

GHL_PRIVATE_TOKEN = os.getenv("GHL_PRIVATE_TOKEN", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_API_BASE = os.getenv("GHL_API_BASE", "https://services.leadconnectorhq.com")

# OAuth app (Step 5), from the Marketplace developer portal: My Apps -> your app -> Advanced Settings -> Auth.
GHL_CLIENT_ID = os.getenv("GHL_CLIENT_ID", "")
GHL_CLIENT_SECRET = os.getenv("GHL_CLIENT_SECRET", "")
# Must match a Redirect URL saved on the app, e.g. https://<app>.onrender.com/oauth/callback
GHL_REDIRECT_URI = os.getenv("GHL_REDIRECT_URI", "")
# Fernet key that encrypts OAuth tokens in the database.
APP_ENCRYPTION_KEY = os.getenv("APP_ENCRYPTION_KEY", "")

# SQLite file. On Render's free tier the disk is temporary: the file resets on every deploy.
DATABASE_PATH = os.getenv("DATABASE_PATH", "speed_to_lead.db")

# Ed25519 public key GHL signs webhooks with (`X-GHL-Signature` header), copied from
# https://marketplace.gohighlevel.com/docs/webhook/WebhookIntegrationGuide
GHL_WEBHOOK_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=
-----END PUBLIC KEY-----
"""
