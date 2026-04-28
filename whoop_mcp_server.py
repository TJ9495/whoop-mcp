import os
import requests
import uvicorn
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
WHOOP_ACCESS_TOKEN = os.getenv("WHOOP_ACCESS_TOKEN")

mcp = FastMCP(
    "whoop-mcp",
    host="0.0.0.0",
    port=PORT,
)

WHOOP_BASE_URL = "https://api.prod.whoop.com/developer/v2"


def whoop_get(path: str, params: dict | None = None):
    if not WHOOP_ACCESS_TOKEN:
        raise ValueError("WHOOP_ACCESS_TOKEN missing")

    response = requests.get(
        f"{WHOOP_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {WHOOP_ACCESS_TOKEN}",
            "Accept": "application/json",
        },
        params=params or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


@mcp.tool()
async def get_latest_recovery() -> str:
    """Get the latest WHOOP recovery."""
    try:
        data = whoop_get("/recovery", {"limit": 1})
        records = data.get("records", [])

        if not records:
            return "No WHOOP recovery data found."

        r = records[0]
        score_state = r.get("score_state", "unknown")
        score = r.get("score", {}) or {}

        if score_state != "SCORED":
            return f"Latest recovery exists but is not scored yet (state: {score_state})."

        recovery_score = score.get("recovery_score", "unknown")
        hrv = score.get("hrv_rmssd_milli", "unknown")
        resting_hr = score.get("resting_heart_rate", "unknown")

        return (
            f"Latest WHOOP recovery: {recovery_score}%\n"
            f"HRV (RMSSD): {hrv}\n"
            f"Resting HR: {resting_hr}\n"
            f"State: {score_state}"
        )
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_latest_cycle() -> str:
    """Get the latest WHOOP cycle."""
    try:
        data = whoop_get("/cycle", {"limit": 1})
        records = data.get("records", [])

        if not records:
            return "No WHOOP cycle data found."

        c = records[0]
        strain = c.get("score", {}).get("strain", "unknown")
        kilojoule = c.get("score", {}).get("kilojoule", "unknown")
        start = c.get("start", "unknown")
        end = c.get("end", "unknown")

        return (
            f"Latest WHOOP cycle:\n"
            f"Start: {start}\n"
            f"End: {end}\n"
            f"Strain: {strain}\n"
            f"Kilojoules: {kilojoule}"
        )
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_recent_workouts(limit: int = 5) -> str:
    """Get recent WHOOP workouts."""
    try:
        data = whoop_get("/activity/workout", {"limit": limit})
        records = data.get("records", [])

        if not records:
            return "No recent WHOOP workouts found."

        lines = []
        for workout in records:
            sport_name = workout.get("sport_name") or workout.get("sport", {}).get("name") or "Workout"
            start = workout.get("start", "unknown")
            end = workout.get("end", "unknown")
            score_state = workout.get("score_state", "unknown")
            strain = workout.get("score", {}).get("strain", "unknown")

            lines.append(
                f"• {sport_name} — {start} to {end} — strain {strain} — {score_state}"
            )

        return "Recent WHOOP workouts:\n" + "\n".join(lines)
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_sleep_for_latest_cycle() -> str:
    """Get sleep data for the latest WHOOP cycle."""
    try:
        cycle_data = whoop_get("/cycle", {"limit": 1})
        cycles = cycle_data.get("records", [])

        if not cycles:
            return "No WHOOP cycle data found."

        cycle_id = cycles[0].get("id")
        if not cycle_id:
            return "Latest cycle found but no cycle ID was returned."

        sleep = whoop_get(f"/cycle/{cycle_id}/sleep")
        score_state = sleep.get("score_state", "unknown")
        score = sleep.get("score", {}) or {}

        sleep_performance = score.get("sleep_performance_percentage", "unknown")
        respiratory_rate = score.get("respiratory_rate", "unknown")
        sleep_needed = score.get("sleep_needed", "unknown")

        return (
            f"Sleep for latest cycle:\n"
            f"Performance: {sleep_performance}\n"
            f"Respiratory rate: {respiratory_rate}\n"
            f"Sleep needed: {sleep_needed}\n"
            f"State: {score_state}"
        )
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.tool()
async def get_recovery_for_latest_cycle() -> str:
    """Get recovery for the latest WHOOP cycle."""
    try:
        cycle_data = whoop_get("/cycle", {"limit": 1})
        cycles = cycle_data.get("records", [])

        if not cycles:
            return "No WHOOP cycle data found."

        cycle_id = cycles[0].get("id")
        if not cycle_id:
            return "Latest cycle found but no cycle ID was returned."

        recovery = whoop_get(f"/cycle/{cycle_id}/recovery")
        score_state = recovery.get("score_state", "unknown")
        score = recovery.get("score", {}) or {}

        if score_state != "SCORED":
            return f"Latest recovery exists but is not scored yet (state: {score_state})."

        recovery_score = score.get("recovery_score", "unknown")
        hrv = score.get("hrv_rmssd_milli", "unknown")
        resting_hr = score.get("resting_heart_rate", "unknown")

        return (
            f"Recovery for latest cycle:\n"
            f"Recovery: {recovery_score}%\n"
            f"HRV (RMSSD): {hrv}\n"
            f"Resting HR: {resting_hr}\n"
            f"State: {score_state}"
        )
    except Exception as e:
        return f"WHOOP error: {str(e)}"


@mcp.resource("whoop://latest-recovery")
async def latest_recovery_resource() -> str:
    """Latest WHOOP recovery resource."""
    return await get_latest_recovery()


async def healthcheck(request):
    return JSONResponse({"ok": True, "service": "whoop-mcp"})


app = Starlette(
    routes=[
        Route("/health", endpoint=healthcheck),
        Mount("/", app=mcp.sse_app()),
    ],
)

if __name__ == "__main__":
    print(f"WHOOP MCP SERVER STARTING ON PORT {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
