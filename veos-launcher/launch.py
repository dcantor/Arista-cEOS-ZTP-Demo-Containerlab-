#!/usr/bin/env python3
"""
Boot a vEOS VM inside this container and bridge each container interface
(eth0 = mgmt, eth1.. = data plane) to a tap that QEMU presents to vEOS as
Management1, Ethernet1, Ethernet2, ...

The container's interfaces (created by Docker for eth0 and by containerlab
for eth1+) are moved into a Linux bridge alongside a freshly created tap;
the IP that Docker assigned to eth0 is removed so vEOS can DHCP for itself
on the same broadcast domain (this is the whole point — it lets dnsmasq
serve real ZTP without NAT).

MACs are deterministic per-node so the dnsmasq reservations are stable.
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

NODE_NAME = os.environ.get("NODE_NAME", "veos")
VEOS_IMG  = os.environ.get("VEOS_IMG", "/vEOS.qcow2")
VEOS_RAM  = int(os.environ.get("VEOS_RAM", "2048"))
VEOS_SMP  = int(os.environ.get("VEOS_SMP", "2"))

# OUI 52:54:00 = QEMU. Last 3 bytes derive from NODE_NAME so a given node
# always gets the same MAC across deploys (and rebuilds).
def mac_for(node: str, intf_index: int) -> str:
    import hashlib
    h = hashlib.sha256(f"{node}:{intf_index}".encode()).hexdigest()
    # Force locally-administered, unicast: bottom 2 bits of first byte = 10
    b1 = (int(h[0:2], 16) & 0xFE) | 0x02
    return f"{b1:02x}:{h[2:4]}:{h[4:6]}:{h[6:8]}:{h[8:10]}:{h[10:12]}"


def run(cmd, check=True, capture=False):
    print(f"  $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def list_eth_interfaces():
    """Return [(name, idx)] for eth0, eth1, ... in numeric order."""
    out = []
    for p in sorted(Path("/sys/class/net").iterdir()):
        m = re.fullmatch(r"eth(\d+)", p.name)
        if m:
            out.append((p.name, int(m.group(1))))
    return sorted(out, key=lambda x: x[1])


def wait_for_interfaces_to_settle(stable_secs: float = 4.0,
                                  poll_interval: float = 1.0,
                                  max_wait: float = 30.0):
    """Containerlab attaches data-plane veths (eth1, eth2, ...) seconds
    AFTER container start, on its own schedule. Wait until the eth*
    interface set is stable for `stable_secs` (and, since real topologies
    always have eth0, until at least eth0 is present).
    """
    start = time.time()
    last_set = None
    last_change = time.time()
    while time.time() - start < max_wait:
        cur = tuple(name for name, _ in list_eth_interfaces())
        if cur != last_set:
            print(f"[{NODE_NAME}] interfaces changed: {cur}", flush=True)
            last_set = cur
            last_change = time.time()
        elif cur and time.time() - last_change >= stable_secs:
            return list_eth_interfaces()
        time.sleep(poll_interval)
    print(f"[{NODE_NAME}] interface settle timeout, proceeding with {last_set}",
          flush=True)
    return list_eth_interfaces()


def get_v4(intf: str) -> str | None:
    r = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "dev", intf],
        capture_output=True, text=True,
    )
    m = re.search(r"inet (\S+)", r.stdout)
    return m.group(1) if m else None


def setup_iface_for_qemu(intf: str, br: str, tap: str):
    """Move `intf`'s netns interface into a bridge with a tap. Strip its IP
    so the VM owns L3 on this broadcast domain. Put `intf` in promiscuous
    mode so frames destined for the VM's MAC (which differs from `intf`'s
    MAC) are accepted instead of dropped at the kernel ingress.
    """
    saved_ip = get_v4(intf)
    if saved_ip:
        run(["ip", "addr", "flush", "dev", intf])
    run(["ip", "link", "add", "name", br, "type", "bridge"])
    run(["ip", "link", "set", intf, "master", br])
    run(["ip", "link", "set", intf, "promisc", "on"])
    run(["ip", "tuntap", "add", "dev", tap, "mode", "tap"])
    run(["ip", "link", "set", tap, "master", br])
    run(["ip", "link", "set", intf, "up"])
    run(["ip", "link", "set", tap, "up"])
    run(["ip", "link", "set", br, "up"])
    return saved_ip  # returned for diagnostic logging only


def main():
    if not Path(VEOS_IMG).exists():
        sys.exit(f"vEOS image {VEOS_IMG} not found (bind-mount it).")

    interfaces = wait_for_interfaces_to_settle()
    if not interfaces:
        sys.exit("no eth* interfaces found in container netns")
    print(f"[{NODE_NAME}] settled interfaces: {interfaces}", flush=True)

    qemu_netargs = []
    for name, idx in interfaces:
        br = f"br-{name}"
        tap = f"tap-{name}"
        saved_ip = setup_iface_for_qemu(name, br, tap)
        mac = mac_for(NODE_NAME, idx)
        print(f"[{NODE_NAME}] {name} (was {saved_ip}) -> bridge {br}, tap {tap}, vm mac {mac}",
              flush=True)
        qemu_netargs += [
            "-netdev", f"tap,id=n{idx},ifname={tap},script=no,downscript=no",
            "-device", f"e1000,netdev=n{idx},mac={mac}",
        ]

    # Persistent qcow2 overlay so the VM's writes (post-ZTP startup-config,
    # reboot state) survive container restarts but the user can wipe with
    # `make destroy && rm -rf data/<node>`.
    overlay_dir = Path("/overlay")
    overlay_dir.mkdir(exist_ok=True)
    overlay = overlay_dir / f"{NODE_NAME}.qcow2"
    if not overlay.exists():
        print(f"[{NODE_NAME}] creating overlay {overlay} backed by {VEOS_IMG}", flush=True)
        run(["qemu-img", "create", "-f", "qcow2",
             "-F", "qcow2", "-b", VEOS_IMG, str(overlay)])

    qemu = [
        "qemu-system-x86_64",
        "-name", NODE_NAME,
        "-enable-kvm",
        "-machine", "pc,accel=kvm",
        "-cpu", "host",
        "-smp", str(VEOS_SMP),
        "-m", str(VEOS_RAM),
        "-drive", f"file={overlay},if=virtio,cache=writeback",
        "-nographic",
        "-serial", "telnet:0.0.0.0:5000,server,nowait",
        "-monitor", "none",
        *qemu_netargs,
    ]
    print(f"[{NODE_NAME}] launching qemu", flush=True)
    p = subprocess.Popen(qemu)

    def _sigterm(sig, frame):
        print(f"[{NODE_NAME}] caught signal {sig}, shutting vm down", flush=True)
        try:
            p.terminate()
        except Exception:
            pass
        # give it a moment then force-kill
        for _ in range(20):
            if p.poll() is not None:
                break
            time.sleep(0.5)
        if p.poll() is None:
            p.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    p.wait()


if __name__ == "__main__":
    main()
