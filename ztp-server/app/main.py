import asyncio
import io
import json
import os
import re
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import dnsmasq as dnsmasq_mgr
import docker_ctl
import leases

CONTENT_ROOT = Path(os.environ.get("ZTP_CONTENT_ROOT", "/ztp-content"))
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/app/static"))
EOS_IMAGES_ROOT = Path(os.environ.get("EOS_IMAGES_ROOT", "/eos_images"))
HOST_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
EOS_IMAGE_RE = re.compile(r"^[A-Za-z0-9._-]+\.swi$")
# Strip ANSI escape sequences (CSI ... final byte) from terminal output so the
# log stream renders cleanly as plain text in the browser.
ANSI_RE = re.compile(rb"\x1B\[[0-?]*[ -/]*[@-~]")

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
    # Make sure /dhcp-state/managed.conf exists even on a clean deploy
    # — dnsmasq's `conf-file=` would error on a missing file otherwise.
    dnsmasq_mgr.regenerate()
    # Keep per-host bootstrap scripts in sync with the latest template
    # (e.g. EOS-upgrade clause); cheap idempotent rewrite at startup.
    _refresh_existing_per_host_scripts()
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
        {"name": "logs", "description": "Ad-hoc bundles of events, leases, and raw container logs for offline triage."},
        {"name": "managed-devices", "description": "User-registered devices: dynamic DHCP reservations + per-host bootfile URL."},
        {"name": "eos-images", "description": "EOS .swi images available to flash during ZTP. Per-device target image is set via PUT /api/devices/<host>/eos-image."},
    ],
)


# ---------- ZTP-facing endpoints (consumed by cEOS) ----------

_ZTP_FILE_RE = re.compile(r"^[a-zA-Z0-9_-]+\.sh$")


@app.get("/ztp/{filename}", response_class=PlainTextResponse, tags=["ztp"])
def ztp_script(filename: str):
    """Serve any per-host ZTP script under ztp-content/ztp/. dnsmasq
    hands the cEOS/vEOS the URL of <hostname>.sh via DHCP option 67.
    """
    if not _ZTP_FILE_RE.match(filename):
        raise HTTPException(400, "filename must be <name>.sh")
    p = CONTENT_ROOT / "ztp" / filename
    if not p.exists():
        raise HTTPException(404, f"no such ztp script: {filename}")
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
    eos_choice = db.list_device_settings()  # {name: image-or-None}
    nodes = docker_ctl.list_ceos_nodes()
    out = []
    seen_names: set[str] = set()
    for n in nodes:
        s = summaries.pop(n["name"], {})
        out.append({
            **n, "source": "topology",
            "eos_image": eos_choice.get(n["name"]),
            **s,
        })
        seen_names.add(n["name"])
    for md in db.list_managed_devices():
        if md["name"] in seen_names:
            continue
        s = summaries.pop(md["name"], {})
        out.append({
            "name": md["name"], "container": None, "status": "external",
            "mac": md["mac"], "ip": md["mgmt_ip"],
            "source": "managed",
            "eos_image": eos_choice.get(md["name"]),
            **s,
        })
    # Hosts seen via /log but not in topology and not user-managed.
    for s in summaries.values():
        out.append({"name": s["host"], "container": None, "status": "absent",
                    "mac": None, "ip": None, "source": "absent",
                    "eos_image": eos_choice.get(s["host"]), **s})
    return out


# ---------- Managed devices ----------

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


class ManagedDeviceCreate(BaseModel):
    name: str
    mac: str
    mgmt_ip: str


def _normalize_mac(mac: str) -> str:
    """Accept aa:bb:.. / aa-bb-.. / aabbcc.. and return canonical lower aa:bb:..."""
    s = mac.strip().lower().replace("-", "").replace(":", "").replace(".", "")
    if len(s) != 12 or not all(c in "0123456789abcdef" for c in s):
        raise ValueError(f"invalid mac address: {mac!r}")
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


_BOOTSTRAP_TEMPLATE = """#!/bin/bash
HOST={name}
SRV=http://172.30.0.20

curl -fsS -X POST "$SRV/log?host=$HOST&event=start" || true

# Optional EOS image upgrade. The /ztp/eos-image/<host> endpoint returns
# either an empty body (skip) or just the .swi filename.
IMAGE=$(curl -fsS "$SRV/ztp/eos-image/$HOST" 2>/dev/null)
if [ -n "$IMAGE" ]; then
    curl -fsS -X POST "$SRV/log?host=$HOST&event=image-download" || true
    curl -fsS "$SRV/eos-images/$IMAGE" -o "/mnt/flash/$IMAGE"
    # Point boot-config at the new image so the post-ZTP reboot uses it.
    echo "SWI=flash:$IMAGE" > /mnt/flash/boot-config
fi

curl -fsS "$SRV/configs/$HOST.cfg" -o /mnt/flash/startup-config
sync
curl -fsS -X POST "$SRV/log?host=$HOST&event=done" || true
exit 0
"""


def _scaffold_per_host_script(name: str) -> None:
    """Drop a per-host /ztp/<name>.sh that mirrors the existing pattern.
    Always writes (even if file exists) so the EOS-upgrade clause stays
    in sync with the template above.
    """
    p = CONTENT_ROOT / "ztp" / f"{name}.sh"
    p.write_text(_BOOTSTRAP_TEMPLATE.format(name=name))
    p.chmod(0o755)


def _refresh_existing_per_host_scripts() -> None:
    """On startup, rewrite all per-host scripts so they pick up the
    latest template (currently: EOS upgrade clause). Called from
    lifespan after dnsmasq regenerate.
    """
    ztp_dir = CONTENT_ROOT / "ztp"
    if not ztp_dir.exists():
        return
    for p in ztp_dir.glob("*.sh"):
        _scaffold_per_host_script(p.stem)


def _scaffold_empty_config(name: str) -> None:
    """Drop a placeholder ztp-content/configs/<name>.cfg the user can edit."""
    p = CONTENT_ROOT / "configs" / f"{name}.cfg"
    if p.exists():
        return
    p.write_text(f"! Placeholder config for {name}. Edit me in the Configs tab.\nhostname {name}\n!\nend\n")


@app.get("/api/managed-devices", tags=["managed-devices"])
def api_managed_devices_list():
    return db.list_managed_devices()


@app.post("/api/managed-devices", tags=["managed-devices"])
async def api_managed_devices_create(body: ManagedDeviceCreate):
    name = body.name.strip()
    if not HOST_RE.match(name):
        raise HTTPException(400, "name must be alphanumeric / dash / underscore")
    if not _IPV4_RE.match(body.mgmt_ip):
        raise HTTPException(400, f"invalid mgmt_ip: {body.mgmt_ip!r}")
    try:
        mac = _normalize_mac(body.mac)
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        row = db.insert_managed_device(name, mac, body.mgmt_ip)
    except Exception as e:
        # UNIQUE violation or similar
        raise HTTPException(409, f"could not add device: {e}")

    _scaffold_per_host_script(name)
    _scaffold_empty_config(name)
    dnsmasq_mgr.regenerate()

    await _broadcast({"type": "managed_device_added", "device": row})
    return row


@app.delete("/api/managed-devices/{name}", tags=["managed-devices"])
async def api_managed_devices_delete(name: str):
    if not HOST_RE.match(name):
        raise HTTPException(400, "invalid name")
    if not db.delete_managed_device(name):
        raise HTTPException(404, "no such managed device")

    # Drop the per-host script (config file is left in place on purpose).
    script = CONTENT_ROOT / "ztp" / f"{name}.sh"
    try:
        script.unlink()
    except FileNotFoundError:
        pass

    dnsmasq_mgr.regenerate()
    await _broadcast({"type": "managed_device_removed", "name": name})
    return {"ok": True, "name": name}


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


@app.get("/api/devices/{host}/logs/stream", tags=["devices"])
async def api_device_logs_stream(host: str, request: Request, tail: int = 200):
    """SSE: live `docker logs -f` for the cEOS container backing this node.
    Each event is one line; the connection stays open until the client
    disconnects or the container goes away.
    """
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    cli = docker_ctl._client()
    container_name = f"clab-{docker_ctl.LAB_NAME}-{host}"
    try:
        c = cli.containers.get(container_name)
    except Exception:
        raise HTTPException(404, "no such device")

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
    log_stream = c.logs(
        stream=True, follow=True, tail=tail, stdout=True, stderr=True
    )

    def reader():
        # docker streams data in tiny chunks (often single bytes for terminal
        # output); buffer until a complete line, strip ANSI, then enqueue.
        buf = b""
        try:
            for chunk in log_stream:
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    text = ANSI_RE.sub(b"", line).decode("utf-8", errors="replace").rstrip("\r")
                    if not queue.full():
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            if buf:
                text = ANSI_RE.sub(b"", buf).decode("utf-8", errors="replace").rstrip("\r")
                if text and not queue.full():
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception:
            pass
        finally:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception:
                pass

    threading.Thread(target=reader, daemon=True).start()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    return
                yield f"data: {item}\n\n"
        finally:
            try:
                log_stream.close()
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/devices/{host}/apply-config", tags=["devices"])
async def api_device_apply_config(host: str):
    """Push the current per-host config into the device's running config
    (and save it). No reboot — uses `configure replace ... force` over
    eAPI to the live vEOS. apply_config is sync (httpx call into the VM
    can take 30-60s), so we run it on a worker thread to keep the event
    loop free for SSE streams.
    """
    try:
        result = await asyncio.to_thread(docker_ctl.apply_config, host)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    await _broadcast({"type": "config_applied", "host": host})
    return result


# ---------- EOS images / per-device target ----------

def _list_eos_images() -> list[dict]:
    if not EOS_IMAGES_ROOT.exists():
        return []
    out = []
    for p in sorted(EOS_IMAGES_ROOT.iterdir()):
        if p.is_file() and p.suffix == ".swi":
            st = p.stat()
            out.append({"filename": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return out


@app.get("/api/eos-images", tags=["eos-images"])
def api_eos_images():
    return _list_eos_images()


@app.get("/eos-images/{filename}", tags=["eos-images"])
def serve_eos_image(filename: str):
    """Serve a .swi image to a device during ZTP."""
    if not EOS_IMAGE_RE.match(filename):
        raise HTTPException(400, "bad filename")
    p = EOS_IMAGES_ROOT / filename
    if not p.exists():
        raise HTTPException(404, "no such image")
    return FileResponse(p, media_type="application/octet-stream", filename=filename)


class DeviceEosImageUpdate(BaseModel):
    eos_image: str | None  # None / "" / missing field = skip upgrade


@app.get("/api/devices/{host}/eos-image", tags=["eos-images"])
def api_device_eos_image_get(host: str):
    """Return the chosen EOS image for this host, or empty if none.
    The bootstrap script consumes this via plain text in `text` param.
    """
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    img = db.get_device_eos_image(host)
    return {"host": host, "eos_image": img}


@app.get("/ztp/eos-image/{host}", tags=["eos-images"], response_class=PlainTextResponse)
def ztp_eos_image_for_host(host: str):
    """Plain-text endpoint the bootstrap script curls to learn its target
    EOS image. Empty body = no upgrade; otherwise just the filename.
    """
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    img = db.get_device_eos_image(host) or ""
    return PlainTextResponse(img, media_type="text/plain")


@app.put("/api/devices/{host}/eos-image", tags=["eos-images"])
async def api_device_eos_image_set(host: str, body: DeviceEosImageUpdate):
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    img = (body.eos_image or "").strip() or None
    if img is not None:
        if not EOS_IMAGE_RE.match(img):
            raise HTTPException(400, "eos_image must be a .swi filename")
        if not (EOS_IMAGES_ROOT / img).exists():
            raise HTTPException(404, f"no such image on server: {img}")
    db.set_device_eos_image(host, img)
    await _broadcast({"type": "eos_image_changed", "host": host, "eos_image": img})
    return {"host": host, "eos_image": img}


@app.get("/api/leases", tags=["leases"])
def api_leases():
    return leases.pool_summary()


@app.get("/api/events", tags=["events"])
def api_events(limit: int = 200):
    return db.list_events(limit=limit)


# ---------- Logs bundle ----------

@app.get("/api/logs/bundle", tags=["logs"])
def api_logs_bundle():
    """Build a .tar.gz bundle in memory containing the ZTP events, the
    DHCP lease snapshot, and the raw docker logs of the dnsmasq and
    ztp-app containers. A small manifest.json describes the contents.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = f"ztp-logs-{ts}"
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    events = db.list_events(limit=100_000)
    pool = leases.pool_summary()

    bundle_files = [
        ("events.json", json.dumps(events, indent=2).encode()),
        ("leases.json", json.dumps(pool, indent=2).encode()),
    ]

    log_sources = [("dnsmasq.log", "ztp-dhcp"), ("app.log", "ztp-http")]
    for filename, short in log_sources:
        data = docker_ctl.container_logs(short)
        if data is None:
            data = f"# container clab-{docker_ctl.LAB_NAME}-{short} not found\n".encode()
        bundle_files.append((filename, data))

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lab": docker_ctl.LAB_NAME,
        "event_count": len(events),
        "lease_count": pool["used"],
        "files": [name for name, _ in bundle_files],
    }
    bundle_files.append(("manifest.json", json.dumps(manifest, indent=2).encode()))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in bundle_files:
            info = tarfile.TarInfo(name=f"{folder}/{name}")
            info.size = len(data)
            info.mtime = now_epoch
            tar.addfile(info, io.BytesIO(data))

    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{folder}.tar.gz"'},
    )


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

    # index.html must never be cached: the JS/CSS it points to is a
    # content-hashed Vite bundle, but if the browser keeps a stale
    # index.html it'll keep loading the OLD bundle hash and never see
    # new UI features. The /assets/* responses are still cacheable
    # (their filenames change on every build), so we only no-store the
    # SPA shell.
    _NO_CACHE_HEADERS = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Reserved API/ZTP prefixes are handled above; anything else falls through here.
        index = STATIC_ROOT / "index.html"
        if not index.exists():
            return PlainTextResponse("UI not built", status_code=503)
        return FileResponse(index, headers=_NO_CACHE_HEADERS)
