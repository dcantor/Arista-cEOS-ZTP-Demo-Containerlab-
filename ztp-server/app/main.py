import asyncio
import docker
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
EOS_IMAGES_ROOT   = Path(os.environ.get("EOS_IMAGES_ROOT",   "/eos_images"))
IOSXE_IMAGES_ROOT = Path(os.environ.get("IOSXE_IMAGES_ROOT", "/iosxe-images"))
NXOS_IMAGES_ROOT  = Path(os.environ.get("NXOS_IMAGES_ROOT",  "/nxos-images"))
HOST_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
# Either an Arista .swi or a Cisco .qcow2 / .bin / .iso image filename.
EOS_IMAGE_RE = re.compile(r"^[A-Za-z0-9._-]+\.(swi|qcow2|bin|iso|tar)$")
# Per-vendor image directories.
_IMAGE_DIR_BY_VENDOR = {
    "arista": EOS_IMAGES_ROOT,
    "cisco":  IOSXE_IMAGES_ROOT,
    "nexus":  NXOS_IMAGES_ROOT,
}
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

_ZTP_FILE_RE = re.compile(r"^[a-zA-Z0-9_-]+\.(sh|py)$")


@app.get("/ztp/{filename}", response_class=PlainTextResponse, tags=["ztp"])
def ztp_script(filename: str):
    """Serve any per-host ZTP script under ztp-content/ztp/. dnsmasq
    hands the device the URL of <hostname>.sh (bash, Arista) or
    <hostname>.py (Python, Cisco IOS-XE guest shell) via DHCP option 67.
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
    vm_statuses = docker_ctl.vm_status_all()
    out = []
    seen_names: set[str] = set()
    for n in nodes:
        s = summaries.pop(n["name"], {})
        out.append({
            **n, "source": "topology",
            "eos_image": eos_choice.get(n["name"]),
            "vm_status": vm_statuses.get(n["name"], "unknown"),
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


class ManagedDeviceUpdate(BaseModel):
    mac: str
    mgmt_ip: str


@app.put("/api/managed-devices/{name}", tags=["managed-devices"])
async def api_managed_devices_update(name: str, body: ManagedDeviceUpdate):
    if not HOST_RE.match(name):
        raise HTTPException(400, "invalid name")
    if not _IPV4_RE.match(body.mgmt_ip):
        raise HTTPException(400, f"invalid mgmt_ip: {body.mgmt_ip!r}")
    try:
        mac = _normalize_mac(body.mac)
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        row = db.update_managed_device(name, mac, body.mgmt_ip)
    except Exception as e:
        # UNIQUE violation on mac or mgmt_ip
        raise HTTPException(409, f"could not update device: {e}")
    if row is None:
        raise HTTPException(404, "no such managed device")

    dnsmasq_mgr.regenerate()
    await _broadcast({"type": "managed_device_updated", "device": row})
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


@app.post("/api/devices/{host}/start", tags=["devices"])
async def api_device_start(host: str):
    """Start the vEOS VM inside the wrapper. Idempotent."""
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    try:
        result = await asyncio.to_thread(docker_ctl.vm_start, host)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    await _broadcast({"type": "vm_started", "host": host})
    return result


@app.post("/api/devices/{host}/stop", tags=["devices"])
async def api_device_stop(host: str):
    """Stop the vEOS VM inside the wrapper. Idempotent."""
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    try:
        result = await asyncio.to_thread(docker_ctl.vm_stop, host)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    await _broadcast({"type": "vm_stopped", "host": host})
    return result


@app.get("/api/devices/{host}/status", tags=["devices"])
def api_device_status(host: str):
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    return {"host": host, "vm_status": docker_ctl.vm_status(host)}


def _strip_telnet_iac(buf: bytes) -> bytes:
    """Strip RFC 854 telnet IAC sequences (negotiation, subneg). Returns
    a clean byte string + a leftover tail (an incomplete sequence at the
    end of buf that we should re-prepend on the next read).
    """
    out = bytearray()
    i = 0
    while i < len(buf):
        b = buf[i]
        if b != 0xff:
            out.append(b)
            i += 1
            continue
        # IAC; need at least one more byte
        if i + 1 >= len(buf):
            break  # tail
        cmd = buf[i + 1]
        if cmd == 0xff:
            out.append(0xff)
            i += 2
        elif cmd in (0xfb, 0xfc, 0xfd, 0xfe):  # WILL/WONT/DO/DONT + opt
            if i + 2 >= len(buf):
                break  # tail
            i += 3
        elif cmd == 0xfa:  # subneg, skip to IAC SE (FF F0)
            end = buf.find(b"\xff\xf0", i + 2)
            if end == -1:
                break  # tail
            i = end + 2
        else:
            i += 2  # other 2-byte command
    tail = bytes(buf[i:])
    return bytes(out), tail


@app.get("/api/devices/{host}/console/stream", tags=["devices"])
async def api_device_console_stream(host: str, request: Request):
    """SSE: stream the VM's serial console.

    QEMU's `-serial telnet:0.0.0.0:5000,server,nowait` chardev gets into
    a stuck "no listener" state after a few client connect/disconnects.
    Instead of opening our own TCP socket to the wrapper, we run
    `docker exec wrapper telnet localhost 5000` inside the wrapper —
    same path that `make console-<node>` uses, which is reliable. The
    docker SDK gives us a generator over the exec's stdout.
    """
    if not HOST_RE.match(host):
        raise HTTPException(400, "invalid host")
    node = next((n for n in docker_ctl.list_ceos_nodes() if n["name"] == host), None)
    if node is None:
        raise HTTPException(404, "no such device")

    cli = docker.from_env()
    try:
        c = cli.containers.get(node["container"])
    except docker.errors.NotFound:
        raise HTTPException(404, "wrapper container not found")

    # `tail -F` the launcher's persistent console capture. The launcher
    # holds the single TCP connection to QEMU's telnet (which only
    # accepts one client at a time) and appends to this file; we follow
    # it here. Multiple browsers can watch concurrently.
    exec_id = cli.api.exec_create(
        c.id,
        ["sh", "-c", "tail -n +1 -F /tmp/qemu-console.log 2>/dev/null"],
        stdout=True, stderr=True, tty=False,
    )["Id"]
    log_stream = cli.api.exec_start(exec_id, stream=True, demux=False)

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=4096)

    def reader():
        buf = b""
        line_buf = b""
        try:
            for chunk in log_stream:
                if not chunk:
                    continue
                buf += chunk
                cleaned, buf = _strip_telnet_iac(buf)
                line_buf += cleaned
                while b"\n" in line_buf:
                    line, _, line_buf = line_buf.partition(b"\n")
                    text = ANSI_RE.sub(b"", line).decode("utf-8", errors="replace").rstrip("\r")
                    if not queue.full():
                        loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception:
            pass
        finally:
            try: loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception: pass

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
            # Kill the in-container `tail -F` so the exec stream ends.
            try:
                c.exec_run(["pkill", "-f", "tail -n +1 -F /tmp/qemu-console.log"])
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

def _list_images_for_vendor(vendor: str) -> list[dict]:
    """List image files for one vendor (Arista .swi or Cisco .qcow2/.bin/.iso).
    Each entry carries its vendor so the UI can filter the dropdown.
    """
    root = _IMAGE_DIR_BY_VENDOR.get(vendor)
    if root is None or not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_file() and EOS_IMAGE_RE.match(p.name):
            st = p.stat()
            out.append({
                "filename": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "vendor": vendor,
            })
    return out


def _list_eos_images() -> list[dict]:
    """Aggregate images across every vendor — backward-compatible name
    (still called eos-images on the API). The UI keys off the per-row
    vendor field to filter the dropdown.
    """
    out = []
    for vendor in _IMAGE_DIR_BY_VENDOR:
        out.extend(_list_images_for_vendor(vendor))
    return out


def _resolve_image_path(filename: str) -> Path | None:
    """Locate `filename` under any vendor's image dir."""
    for root in _IMAGE_DIR_BY_VENDOR.values():
        if not root.exists():
            continue
        p = root / filename
        if p.exists():
            return p
    return None


@app.get("/api/eos-images", tags=["eos-images"])
def api_eos_images():
    return _list_eos_images()


@app.get("/eos-images/{filename}", tags=["eos-images"])
def serve_eos_image(filename: str):
    """Serve an EOS .swi or IOS-XE .qcow2/.bin/.iso to a device during ZTP."""
    if not EOS_IMAGE_RE.match(filename):
        raise HTTPException(400, "bad filename")
    p = _resolve_image_path(filename)
    if p is None:
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
            raise HTTPException(400, "eos_image must be a recognized image filename")
        if _resolve_image_path(img) is None:
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
