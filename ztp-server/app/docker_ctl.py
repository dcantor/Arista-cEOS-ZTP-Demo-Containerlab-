import docker

CLAB_LABEL = "clab-node-name"
LAB_NAME = "ztp-universal-demo"


def _client():
    return docker.from_env()


def list_ceos_nodes() -> list[dict]:
    """Return cEOS containers in the lab with their current MAC + IP."""
    cli = _client()
    out = []
    for c in cli.containers.list(all=True, filters={"label": f"clab-node-kind=ceos"}):
        labels = c.labels
        if labels.get("containerlab") != LAB_NAME:
            continue
        net = c.attrs["NetworkSettings"]["Networks"].get("ztp-mgmt", {})
        out.append({
            "name": labels.get(CLAB_LABEL, c.name),
            "container": c.name,
            "status": c.status,
            "mac": net.get("MacAddress"),
            "ip": net.get("IPAddress"),
        })
    return sorted(out, key=lambda n: n["name"])


def container_logs(short_name: str, tail: int = 5000) -> bytes | None:
    """Return raw docker logs for a node in the lab, or None if missing."""
    cli = _client()
    container_name = f"clab-{LAB_NAME}-{short_name}"
    try:
        c = cli.containers.get(container_name)
    except docker.errors.NotFound:
        return None
    return c.logs(stream=False, tail=tail, stdout=True, stderr=True, timestamps=True)


def apply_config(node_name: str, server_url: str = "http://172.30.0.20") -> dict:
    """Hot-swap the cEOS running config to the per-host file the ZTP
    server is currently serving, then save it to startup-config.

    Uses `Cli configure replace <url> force` + `write memory` inside the
    container — eAPI-free, no reboot, no netns teardown, no veth loss.
    Equivalent to "make the device match what is in
    ztp-content/configs/<node>.cfg" without re-running ZTP.
    """
    cli = _client()
    container_name = f"clab-{LAB_NAME}-{node_name}"
    try:
        c = cli.containers.get(container_name)
    except docker.errors.NotFound:
        raise ValueError(f"unknown node: {node_name}")

    url = f"{server_url}/configs/{node_name}.cfg"
    # FastCli (not Cli) bypasses AAA "Default authorization rejects all"
    # which trips on the lab's no-aaa-root configs.
    cmd = [
        "FastCli", "-p", "15",
        "-c", f"configure replace {url} force",
        "-c", "write memory",
    ]
    rc, out = c.exec_run(cmd)
    raw = out.decode("utf-8", errors="replace") if out else ""
    # Strip ANSI sequences and other non-ASCII so the JSON response body
    # length matches Content-Length (uvicorn enforces it strictly).
    import re as _re
    output = _re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
    output = output.encode("ascii", errors="replace").decode("ascii")
    if rc != 0:
        raise RuntimeError(f"apply_config failed (rc={rc}): {output}")
    return {
        "node": node_name,
        "container": container_name,
        "status": "applied",
        "source_url": url,
        "output_tail": output[-500:],
    }
