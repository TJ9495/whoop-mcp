import os
import secrets
import requests
import uvicorn
from datetime import datetime
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, PlainTextResponse
from starlette.routing import Mount, Route

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
WHOOP_CLIENT_ID = os.getenv("WHOOP_CLIENT_ID")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")
WHOOP_REFRESH_TOKEN = os.getenv("WHOOP_REFRESH_TOKEN")
WHOOP_REDIRECT_URI = os.getenv("WHOOP_REDIRECT_URI", f"http://localhost:{PORT}/callback")
WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_BASE_URL = "https://api.prod.whoop.com/developer/v2"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

_token_cache = {"access_token": None, "expires_at": 0}

mcp = FastMCP("whoop-mcp", host="0.0.0.0", port=PORT)

def _set_refresh_token(token: str | None):
    global WHOOP_REFRESH_TOKEN
    WHOOP_REFRESH_TOKEN = token


def refresh_whoop_token():
    global _token_cache
    if WHOOP_REFRESH_TOKEN and WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": WHOOP_REFRESH_TOKEN,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
            },
            timeout=30,
        )
        if response.status_code == 200:
            tokens = response.json()
            _token_cache["access_token"] = tokens.get("access_token")
            _token_cache["expires_at"] = datetime.now().timestamp() + tokens.get("expires_in", 3600) - 300
            new_refresh = tokens.get("refresh_token")
            if new_refresh:
                _set_refresh_token(new_refresh)
            return True
    return False


def get_whoop_token():
    now = datetime.now().timestamp()
    if _token_cache["access_token"] is None or now >= _token_cache["expires_at"]:
        if not refresh_whoop_token():
            raise ValueError("Failed to get WHOOP token")
    return _token_cache["access_token"]


def whoop_get(path: str, params: dict | None = None):
    token = get_whoop_token()
    response = requests.get(
        f"{WHOOP_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or {},
        timeout=30,
    )
    if response.status_code == 401:
        _token_cache["access_token"] = None
        raise ValueError("WHOOP token invalid and refresh failed")
    response.raise_for_status()
    return response.json()


def build_auth_url(state: str):
    if not WHOOP_CLIENT_ID:
        raise ValueError("WHOOP_CLIENT_ID is not set")
    scopes = "offline"
    params = {
        "response_type": "code",
        "client_id": WHOOP_CLIENT_ID,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "scope": scopes,
        "state": state,
    }
    from urllib.parse import urlencode
    return f"{WHOOP_AUTH_URL}?{urlencode(params)}"


async def auth_start(request):
    state = secrets.token_urlsafe(16)
    request.app.state.oauth_state = state
    try:
        url = build_auth_url(state)
    except Exception as e:
        return PlainTextResponse(f"Auth error: {e}", status_code=500)
    return RedirectResponse(url)


async def auth_callback(request):
    error = request.query_params.get("error")
    if error:
        return PlainTextResponse(f"WHOOP authorization failed: {error}", status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    expected_state = getattr(request.app.state, "oauth_state", None)

    if expected_state and state != expected_state:
        return PlainTextResponse("Invalid OAuth state", status_code=400)

    if not code:
        return PlainTextResponse("Missing authorization code", status_code=400)

    if not WHOOP_CLIENT_ID or not WHOOP_CLIENT_SECRET:
        return PlainTextResponse("Missing WHOOP client credentials", status_code=500)

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": WHOOP_REDIRECT_URI,
            "client_id": WHOOP_CLIENT_ID,
            "client_secret": WHOOP_CLIENT_SECRET,
        },
        timeout=30,
    )

    if response.status_code != 200:
        return PlainTextResponse(f"Token exchange failed: {response.text}", status_code=400)

    tokens = response.json()
    _token_cache["access_token"] = tokens.get("access_token")
    _token_cache["expires_at"] = datetime.now().timestamp() + tokens.get("expires_in", 3600) - 300

    new_refresh = tokens.get("refresh_token")
    if new_refresh:
        _set_refresh_token(new_refresh)

    return JSONResponse({
        "ok": True,
        "message": "WHOOP re-auth complete",
        "has_refresh_token": bool(WHOOP_REFRESH_TOKEN)
    })


@mcp.tool()
async def get_latest_recovery() -> str:
    try:
        data = whoop_get("/recovery", {"limit": 1})
        records = data.get("records", [])

        if not records:
            return "No WHOOP recovery data found."

        r = records[0]
        score_state = r.get("score_state", "unknown")
        score = r.get("score", {}) or {}

        if score_state != "SCORED":
            return f"Latest recovery not scored yet (state: {score_state})."

        recovery_score = score.get("recovery_score")
        hrv = score.get("hrv_rmssd_milli")
        resting_hr = score.get("resting_heart_rate")

        return f"Latest WHOOP recovery: **{recovery_score}%**\nHRV: {hrv}ms\nResting HR: {resting_hr}bpm"
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_latest_cycle() -> str:
    try:
        data = whoop_get("/cycle", {"limit": 1})
        records = data.get("records", [])

        if not records:
            return "No WHOOP cycle data found."

        c = records[0]
        strain = c.get("score", {}).get("strain")
        kilojoule = c.get("score", {}).get("kilojoule")
        start = c.get("start")

        return f"Latest WHOOP cycle: **Strain {strain}**, **{kilojoule}kJ** (started {start})"
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_sleep_for_latest_cycle() -> str:
    try:
        data = whoop_get("/cycle", {"limit": 1})
        cycles = data.get("records", [])

        if not cycles:
            return "No WHOOP cycle data found."

        cycle_id = cycles[0].get("id")
        sleep = whoop_get(f"/cycle/{cycle_id}/sleep")
        score = sleep.get("score", {})

        return (
            f"Sleep performance: **{score.get('sleep_performance_percentage')}%**\n"
            f"Duration: {score.get('sleep_duration')}h\n"
            f"Respiratory rate: {score.get('respiratory_rate')}rpm"
        )
    except Exception as e:
        return f"WHOOP error: {str(e)}"


async def healthcheck(request):
    return JSONResponse({
        "ok": True,
        "service": "whoop-mcp",
        "token_status": "ready" if _token_cache["access_token"] else "needs_auth"
    })


app = Starlette(
    routes=[
        Route("/health", endpoint=healthcheck),
        Route("/auth", endpoint=auth_start),
        Route("/callback", endpoint=auth_callback),
        Mount("/", app=mcp.sse_app()),
    ],
)

if __name__ == "__main__":
    print(f"WHOOP MCP SERVER STARTING ON PORT {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
