"""Standalone OAuth redirect service for Alpenglow VGC mobile login."""

import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

OAUTH_APP_REDIRECT = os.environ.get(
    "OAUTH_APP_REDIRECT", "com.alpenglow.vgc.app://oauth-callback"
)

app = FastAPI(title="Alpenglow VGC OAuth redirect", version="1.0.0")


@app.get("/")
async def root() -> dict:
    return {"ok": True}


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    qs = str(request.query_params)
    target = f"{OAUTH_APP_REDIRECT}?{qs}" if qs else OAUTH_APP_REDIRECT
    return RedirectResponse(url=target, status_code=302)