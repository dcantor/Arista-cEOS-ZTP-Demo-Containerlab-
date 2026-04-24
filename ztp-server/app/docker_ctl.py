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


def reprovision(node_name: str) -> dict:
    """Clear /mnt/flash/startup-config on the cEOS container and restart it
    so ZTP runs again on the next boot.
    """
    cli = _client()
    container_name = f"clab-{LAB_NAME}-{node_name}"
    try:
        c = cli.containers.get(container_name)
    except docker.errors.NotFound:
        raise ValueError(f"unknown node: {node_name}")
    rc, out = c.exec_run(["sh", "-c", "rm -f /mnt/flash/startup-config && truncate -s 0 /mnt/flash/startup-config"])
    if rc != 0:
        raise RuntimeError(f"failed to clear startup-config: {out.decode(errors='replace')}")
    c.restart(timeout=10)
    return {"node": node_name, "container": container_name, "status": "restarting"}
