"""Docker/container helpers for the ZTP UI.

Runs inside the ztp-app container (mounts /var/run/docker.sock). Queries
and mutates the vEOS wrapper containers of the same containerlab lab.
"""
import docker

CLAB_LABEL = "clab-node-name"
LAB_NAME = "ztp-universal-demo"
# Short names of the vEOS nodes in the topology. Used to find/enumerate
# them since the containers are kind:linux and don't self-identify as EOS.
VEOS_NODES = ("spine1", "spine2", "leaf1", "leaf2")


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
            # The Docker-assigned MAC on the wrapper is meaningless for the
            # VM; the VM synthesizes its own MAC deterministically from the
            # node name. Report the wrapper's MAC for transparency.
            "mac": net.get("MacAddress"),
            "ip": net.get("IPAddress"),
        })
    return sorted(out, key=lambda n: n["name"])


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
    """
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
