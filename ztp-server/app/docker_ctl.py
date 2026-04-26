"""Docker/container helpers for the ZTP UI.

Runs inside the ztp-app container (mounts /var/run/docker.sock). Queries
and mutates the vEOS wrapper containers of the same containerlab lab.
"""
import docker

CLAB_LABEL = "clab-node-name"
LAB_NAME = "ztp-universal-demo"
# Per-vendor static map of topology node -> vendor. Enumerated explicitly
# (rather than parsed from topology.clab.yml at runtime) because the file
# isn't bind-mounted into the app container.
TOPOLOGY_VENDORS = {
    "spine1": "arista",
    "spine2": "arista",
    "leaf1":  "arista",
    "leaf2":  "arista",
    "leaf101": "cisco",
}
# Short names of the *topology* nodes (any vendor). Used to enumerate
# devices since the wrapper containers are kind:linux and don't
# self-identify as a network OS.
VEOS_NODES = tuple(TOPOLOGY_VENDORS.keys())


def _client():
    return docker.from_env()


def list_ceos_nodes() -> list[dict]:
    """Return the vEOS wrapper containers in the lab with mgmt MAC + IP.
    Name kept as `list_ceos_nodes` for API compat with the old cEOS lab;
    now returns the vEOS launcher containers.
    """
    cli = _client()
    out = []
    for node in VEOS_NODES:
        name = f"clab-{LAB_NAME}-{node}"
        try:
            c = cli.containers.get(name)
        except docker.errors.NotFound:
            continue
        net = c.attrs["NetworkSettings"]["Networks"].get("ztp-mgmt", {})
        out.append({
            "name": node,
            "container": c.name,
            "status": c.status,
            "vendor": TOPOLOGY_VENDORS.get(node, "arista"),
            # The Docker-assigned MAC on the wrapper is meaningless for the
            # VM; the VM synthesizes its own MAC deterministically from the
            # node name. Report the wrapper's MAC for transparency.
            "mac": net.get("MacAddress"),
            "ip": net.get("IPAddress"),
        })
    return sorted(out, key=lambda n: n["name"])


def _exec(short_name: str, cmd: list[str]) -> tuple[int, str]:
    """Helper: docker exec a command in the named lab container, return (rc, stdout+stderr)."""
    cli = _client()
    container_name = f"clab-{LAB_NAME}-{short_name}"
    try:
        c = cli.containers.get(container_name)
    except docker.errors.NotFound:
        raise ValueError(f"unknown node: {short_name}")
    rc, out = c.exec_run(cmd)
    return rc, (out.decode("utf-8", errors="replace") if out else "")


def vm_start(node_name: str) -> dict:
    """Start the vEOS VM inside the named wrapper container (idempotent)."""
    rc, out = _exec(node_name, ["/usr/local/bin/vm-start.sh"])
    if rc != 0:
        raise RuntimeError(f"vm-start failed (rc={rc}): {out.strip()}")
    return {"node": node_name, "vm_status": "running", "output": out.strip()}


def vm_stop(node_name: str) -> dict:
    """Stop the vEOS VM (graceful SIGTERM, then SIGKILL after 15 s)."""
    rc, out = _exec(node_name, ["/usr/local/bin/vm-stop.sh"])
    if rc != 0:
        raise RuntimeError(f"vm-stop failed (rc={rc}): {out.strip()}")
    return {"node": node_name, "vm_status": "stopped", "output": out.strip()}


def vm_status(node_name: str) -> str:
    """Return 'running', 'stopped', or 'unknown' (container not present)."""
    try:
        rc, out = _exec(node_name, ["/usr/local/bin/vm-status.sh"])
    except ValueError:
        return "unknown"
    if rc != 0:
        return "unknown"
    s = out.strip()
    return s if s in ("running", "stopped") else "unknown"


def vm_status_all() -> dict[str, str]:
    """Return {node_name: status} for every lab vEOS wrapper."""
    return {n: vm_status(n) for n in VEOS_NODES}


def container_logs(short_name: str, tail: int = 5000) -> bytes | None:
    """Return raw docker logs for a node in the lab, or None if missing.
    For vEOS nodes this is the launcher's output (bridge setup + qemu
    stderr); real vEOS boot logs live on the serial console (port 5000).
    """
    cli = _client()
    container_name = f"clab-{LAB_NAME}-{short_name}"
    try:
        c = cli.containers.get(container_name)
    except docker.errors.NotFound:
        return None
    return c.logs(stream=False, tail=tail, stdout=True, stderr=True, timestamps=True)


def apply_config(node_name: str, server_url: str = "http://172.30.0.20") -> dict:
    """Hot-swap the vEOS running config to the per-host file the ZTP
    server is currently serving, then save it to startup-config. Uses
    eAPI (HTTP JSON-RPC) over the device's post-ZTP management IP. No
    VM reboot, no container restart.

    Arista-only: Cisco IOS-XE has no eAPI. To regenerate a Cisco device's
    config you currently need to Stop+Start it (which re-runs ZTP).
    """
    if TOPOLOGY_VENDORS.get(node_name, "arista") != "arista":
        raise ValueError(
            f"apply-config (live) is Arista-only; {node_name} is "
            f"{TOPOLOGY_VENDORS.get(node_name)}. Stop+Start to re-run ZTP."
        )
    # Resolve mgmt IP: topology nodes have static post-ZTP IPs; managed
    # devices come from the SQLite table (added via the UI).
    topology_ip_by_node = {
        "spine1": "172.30.0.101",
        "spine2": "172.30.0.102",
        "leaf1":  "172.30.0.103",
        "leaf2":  "172.30.0.104",
    }
    mgmt_ip = topology_ip_by_node.get(node_name)
    if mgmt_ip is None:
        # Lazy import keeps docker_ctl import-clean for the cron-like
        # callers that don't need the DB.
        import db
        for d in db.list_managed_devices():
            if d["name"] == node_name:
                mgmt_ip = d["mgmt_ip"]
                break
    if mgmt_ip is None:
        raise ValueError(f"unknown node: {node_name}")

    import httpx

    url = f"{server_url}/configs/{node_name}.cfg"
    # vEOS exposes eAPI on HTTPS:443 by default with a self-signed cert.
    eapi_url = f"https://{mgmt_ip}/command-api"
    body = {
        "jsonrpc": "2.0",
        "method": "runCmds",
        "id": 1,
        "params": {
            "version": 1,
            "format": "text",
            "cmds": [
                "enable",
                f"configure replace {url} force",
                "copy running-config startup-config",
            ],
        },
    }
    # Credentials match the lab's per-host configs.
    auth = ("admin", "admin")
    try:
        # configure replace can take 30-60s on a busy vEOS host with
        # multiple VMs contending for CPU; give it real headroom.
        r = httpx.post(eapi_url, json=body, auth=auth, timeout=120.0, verify=False)
    except Exception as e:
        raise RuntimeError(f"eAPI request failed: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"eAPI {r.status_code}: {r.text[:500]}")
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"eAPI error: {data['error']}")

    raw_tail = str(data.get("result", ""))[-500:]
    # uvicorn computes Content-Length in bytes; ensure ASCII so string len
    # and byte len match.
    safe_tail = raw_tail.encode("ascii", "replace").decode("ascii")
    return {
        "node": node_name,
        "container": f"clab-{LAB_NAME}-{node_name}",
        "status": "applied",
        "source_url": url,
        "output_tail": safe_tail,
    }
