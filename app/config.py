"""Settings. The only module that reads environment variables (and `.env`)."""

import os

from dotenv import load_dotenv

load_dotenv()

GHL_PRIVATE_TOKEN = os.getenv("GHL_PRIVATE_TOKEN", "")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "")
GHL_API_BASE = os.getenv("GHL_API_BASE", "https://services.leadconnectorhq.com")
