import asyncio
import json
import os
import re
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import docker_ctl
import leases

CONTENT_ROOT = Path(os.environ.get("ZTP_CONTENT_ROOT", "/ztp-content"))
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/app/static"))
HOST_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Fan-out for SSE: every connected client gets its own asyncio.Queue.
_subscribers: set[asyncio.Queue] = set()


async def _broadcast(payload: dict) -> None:
    msg = f"data: {json.dumps(payload)}\n\n"
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(
    title="ZTP Server API",
    description=(
        "Backend API for the Arista cEOS ZTP demo. Serves the cEOS-facing "
        "ZTP endpoints (`/ztp/bootstrap.sh`, `/configs/<host>.cfg`, `/log`) "
        "as well as the JSON API consumed by the React dashboard."
    ),
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "ztp", "description": "Endpoints called by cEOS during ZTP."},
        {"name": "devices", "description": "Inventory + per-device actions (re-provision)."},
        {"name": "configs", "description": "Per-host EOS configs served by ZTP. Editable."},
        {"name": "leases", "description": "DHCP pool view (parsed from dnsmasq.leases)."},
        {"name": "events", "description": "ZTP event log persisted in SQLite."},
        {"name": "stream", "description": "Server-Sent Events fan-out for live dashboard updates."},
    ],
)


# ---------- ZTP-facing endpoints (consumed by cEOS) ----------

@app.get("/ztp/bootstrap.sh", response_class=PlainTextResponse, tags=["ztp"])
def ztp_bootstrap():
    p = CONTENT_ROOT / "ztp" / "bootstrap.sh"
    if not p.exists():
        raise HTTPException(404, "bootstrap.sh missing")
    return PlainTextResponse(p.read_text(), media_type="text/plain")


@app.get("/configs/{name}", response_class=PlainTextResponse, tags=["ztp"])
def ztp_config(name: str):
    if not name.endswith(".cfg"):
        raise HTTPException(400, "name must end with .cfg")
    host = name[:-4]
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    p = CONTENT_ROOT / "configs" / name
    if not p.exists():
        raise HTTPException(404, f"no config for {host}")
    return PlainTextResponse(p.read_text(), media_type="text/plain")


@app.api_route("/log", methods=["GET", "POST"], tags=["ztp"])
async def ztp_log(request: Request):
    host = request.query_params.get("host", "unknown")
    event = request.query_params.get("event", "unknown")
    ip = request.client.host if request.client else None
    row = db.insert_event(host, event, ip)
    await _broadcast({"type": "event", "event": row})
    return JSONResponse({"ok": True}, status_code=204)


# ---------- API: inventory, configs, leases, events ----------

@app.get("/api/devices", tags=["devices"])
def api_devices():
    summaries = {s["host"]: s for s in db.host_summaries()}
    nodes = docker_ctl.list_ceos_nodes()
    out = []
    for n in nodes:
        s = summaries.pop(n["name"], {})
        out.append({**n, **s})
    # Hosts seen via /log but not currently a container (e.g. destroyed)
    for s in summaries.values():
        out.append({"name": s["host"], "container": None, "status": "absent",
                    "mac": None, "ip": None, **s})
    return out


@app.get("/api/configs", tags=["configs"])
def api_configs():
    cfg_dir = CONTENT_ROOT / "configs"
    if not cfg_dir.exists():
        return []
    out = []
    for p in sorted(cfg_dir.glob("*.cfg")):
        st = p.stat()
        out.append({
            "host": p.stem,
            "filename": p.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
        })
    return out


@app.get("/api/configs/{host}", tags=["configs"])
def api_config_get(host: str):
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    p = CONTENT_ROOT / "configs" / f"{host}.cfg"
    if not p.exists():
        raise HTTPException(404, "no such config")
    return {"host": host, "content": p.read_text()}


class ConfigUpdate(BaseModel):
    content: str


@app.put("/api/configs/{host}", tags=["configs"])
async def api_config_put(host: str, body: ConfigUpdate):
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    p = CONTENT_ROOT / "configs" / f"{host}.cfg"
    p.write_text(body.content)
    await _broadcast({"type": "config_updated", "host": host})
    return {"ok": True, "size": p.stat().st_size}


@app.post("/api/devices/{host}/reprovision", tags=["devices"])
async def api_device_reprovision(host: str):
    try:
        result = docker_ctl.reprovision(host)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    await _broadcast({"type": "reprovision", "host": host})
    return result


@app.get("/api/leases", tags=["leases"])
def api_leases():
    return leases.pool_summary()


@app.get("/api/events", tags=["events"])
def api_events(limit: int = 200):
    return db.list_events(limit=limit)


# ---------- SSE ----------

@app.get("/api/stream", tags=["stream"])
async def api_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers.add(queue)

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- React SPA ----------

if STATIC_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_ROOT / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Reserved API/ZTP prefixes are handled above; anything else falls through here.
        index = STATIC_ROOT / "index.html"
        if not index.exists():
            return PlainTextResponse("UI not built", status_code=503)
        return FileResponse(index)
