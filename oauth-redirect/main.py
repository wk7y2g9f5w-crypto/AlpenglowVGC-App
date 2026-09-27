"""Standalone OAuth redirect service for Alpenglow VGC mobile login.

Discord does not accept custom URL schemes (myapp://...) as OAuth redirect
URIs. Register this service's https URL + /oauth/callback in the Discord
Developer Portal instead; it bounces the browser back into the app via the
custom scheme, preserving the query string (?code=... / ?error=...).
"""

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
