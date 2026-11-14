from fastapi import FastAPI

app = FastAPI(title="GHL Speed-to-Lead")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
