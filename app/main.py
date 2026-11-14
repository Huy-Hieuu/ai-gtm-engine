import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app import db, webhooks

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="GHL Speed-to-Lead", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
