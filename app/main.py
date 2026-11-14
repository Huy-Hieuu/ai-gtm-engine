import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import agent, db, ghl, webhooks

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app.main")


# How often the app checks for leads due a nudge (Step 3, NO REPLY).
NUDGE_CHECK_SECONDS = 60


async def nudge_loop() -> None:
    while True:
        await asyncio.sleep(NUDGE_CHECK_SECONDS)
        try:
            await asyncio.to_thread(agent.send_nudges)
        except Exception:
            log.exception("nudge check failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    nudges = asyncio.create_task(nudge_loop())
    yield
    nudges.cancel()


app = FastAPI(title="GHL Speed-to-Lead", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/oauth/install")
def oauth_install() -> RedirectResponse:
    """Send the agency admin to GHL to pick a sub-account and approve the scopes."""
    try:
        return RedirectResponse(ghl.install_url())
    except ghl.GHLError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/oauth/callback")
def oauth_callback(code: str) -> dict:
    """GHL lands here after consent with `?code=`; swap it for tokens and save the install."""
    try:
        location_id = ghl.exchange_code(code)
    except ghl.GHLError as e:
        log.warning("oauth install failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    log.info("oauth installed location=%s", location_id)
    return {"status": "installed", "locationId": location_id}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks) -> dict:
    body = await request.body()
    if not webhooks.signature_ok(body, request.headers.get(webhooks.SIGNATURE_HEADER)):
        log.warning("webhook rejected: bad or missing %s", webhooks.SIGNATURE_HEADER)
        raise HTTPException(status_code=401, detail="invalid signature")

    event = json.loads(body)
    event_type = event.get("type")
    webhook_id = webhooks.event_id(event, body)

    if event_type not in webhooks.HANDLERS:
        log.info("webhook ignored id=%s type=%s", webhook_id, event_type)
        return {"status": "ignored"}
    if not db.insert_event(webhook_id, event_type):
        log.info("webhook duplicate skipped id=%s type=%s", webhook_id, event_type)
        return {"status": "duplicate"}

    log.info("webhook received id=%s type=%s", webhook_id, event_type)
    background.add_task(webhooks.handle, webhook_id, event)
    return {"status": "accepted"}
