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
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

NODE_NAME = os.environ.get("NODE_NAME", "veos")
VENDOR    = os.environ.get("VENDOR", "arista").lower()  # "arista" or "cisco"
# /vm-image.qcow2 is the canonical bind path; VEOS_IMG kept for backward compat.
VM_IMG    = os.environ.get("VM_IMG") or os.environ.get("VEOS_IMG") or "/vm-image.qcow2"
VEOS_RAM  = int(os.environ.get("VEOS_RAM", "2048"))
VEOS_SMP  = int(os.environ.get("VEOS_SMP", "2"))
VM_AUTOSTART = os.environ.get("VM_AUTOSTART", "false").lower() in ("true", "1", "yes")
QEMU_CMD_FILE = Path("/tmp/qemu-cmd.sh")

# Vendor profile: per-vendor QEMU defaults. Anything not overridden by env
# falls back here.
_VENDOR_PROFILES = {
    "arista": {
        "ram": 2048,
        "smp": 2,
        "disk_if": "virtio",
        "nic": "e1000",
        "default_img_fallback": "/vEOS.qcow2",
    },
    "cisco": {
        # CSR1000v needs more RAM and IDE disk attachment.
        "ram": 4096,
        "smp": 2,
        "disk_if": "ide",
        "nic": "e1000",
        "default_img_fallback": "/csr.qcow2",
    },
    "nexus": {
        # Nexus 9300v / 9000v: 10 GB RAM, 4 vCPUs, UEFI, AHCI/SATA
        # disk topology (NOT virtio or plain IDE). vrnetlab's working
        # recipe (cisco/n9kv/docker/launch.py); the AHCI bus is what the
        # bootloader expects when it looks for "bootflash".
        "ram": 10240,
        "smp": 4,
        "disk_if": "ahci",   # consumed by main; we override the -drive args
        "nic": "e1000",
        "firmware": "uefi",
        "default_img_fallback": "/nxos.qcow2",
    },
}

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
    """Move `intf`'s netns interface into a bridge with a tap, then put
    the original IP back on the bridge so the wrapper container is still
    reachable from the host (for `docker exec`, port forwards, VNC, etc.).
    The VM gets to own its OWN L3 (via DHCP/static config to a different
    IP) on the same L2 — both wrapper and VM share the bridge, distinct
    MACs, distinct IPs. `intf` goes promiscuous so frames destined for
    the VM's MAC are accepted instead of dropped at kernel ingress.
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
    # Reattach the IP to the bridge so the wrapper container retains
    # L3 connectivity (docker-proxy / VNC port forwards / SSH would
    # otherwise fail because the container has no reachable address).
    if saved_ip:
        run(["ip", "addr", "add", saved_ip, "dev", br])
    return saved_ip


def main():
    profile = _VENDOR_PROFILES.get(VENDOR)
    if profile is None:
        sys.exit(f"unknown VENDOR {VENDOR!r}; supported: {sorted(_VENDOR_PROFILES)}")
    img_path = Path(VM_IMG)
    if not img_path.exists():
        # Fall back to vendor-default mount point (back-compat for arista
        # topologies that bind to /vEOS.qcow2 without setting VM_IMG).
        alt = Path(profile["default_img_fallback"])
        if alt.exists():
            img_path = alt
        else:
            sys.exit(f"VM image not found at {VM_IMG} or {alt} (bind-mount it).")
    print(f"[{NODE_NAME}] vendor={VENDOR} image={img_path} "
          f"ram={profile['ram']}MB smp={profile['smp']} disk_if={profile['disk_if']}",
          flush=True)

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
            "-device", f"{profile['nic']},netdev=n{idx},mac={mac}",
        ]

    # Persistent qcow2 overlay so the VM's writes (post-ZTP startup-config,
    # reboot state) survive container restarts but the user can wipe with
    # `make destroy && rm -rf data/<node>`.
    overlay_dir = Path("/overlay")
    overlay_dir.mkdir(exist_ok=True)
    overlay = overlay_dir / f"{NODE_NAME}.qcow2"
    if not overlay.exists():
        print(f"[{NODE_NAME}] creating overlay {overlay} backed by {img_path}", flush=True)
        run(["qemu-img", "create", "-f", "qcow2",
             "-F", "qcow2", "-b", str(img_path), str(overlay)])

    # Per-vendor RAM/SMP can be overridden via env (VEOS_RAM/VEOS_SMP).
    ram = VEOS_RAM if "VEOS_RAM" in os.environ else profile["ram"]
    smp = VEOS_SMP if "VEOS_SMP" in os.environ else profile["smp"]

    qemu = [
        "qemu-system-x86_64",
        "-name", NODE_NAME,
        "-enable-kvm",
        "-machine", "pc,accel=kvm",
        "-cpu", "host",
        "-smp", str(smp),
        "-m", str(ram),
        "-serial", "telnet:0.0.0.0:5000,server,nowait",
        "-monitor", "none",
    ]
    # UEFI firmware (OVMF) for vendors whose images are GPT-partitioned
    # and won't boot under legacy SeaBIOS. We give the VM a *copy* of
    # OVMF_VARS so each VM has its own NVRAM.
    if profile.get("firmware") == "uefi":
        ovmf_code = "/usr/share/OVMF/OVMF_CODE.fd"
        ovmf_vars_template = "/usr/share/OVMF/OVMF_VARS.fd"
        per_node_vars = overlay_dir / f"{NODE_NAME}_OVMF_VARS.fd"
        if not per_node_vars.exists():
            run(["cp", ovmf_vars_template, str(per_node_vars)])
        qemu += [
            "-drive", f"if=pflash,format=raw,readonly=on,file={ovmf_code}",
            "-drive", f"if=pflash,format=raw,file={per_node_vars}",
        ]
    # Main disk attachment. Vendors that need AHCI/SATA (nexus) use a
    # specific block device topology — the shorthand `-drive ... if=ide`
    # or `if=virtio` is NOT what their bootloader expects. Mirror the
    # vrnetlab n9kv recipe.
    if profile["disk_if"] == "ahci":
        qemu += [
            "-boot", "c",
            "-drive", f"file={overlay},if=none,id=drive-sata-disk0,format=qcow2,cache=writeback",
            "-device", "ahci,id=ahci0,bus=pci.0",
            "-device", "ide-hd,drive=drive-sata-disk0,bus=ahci0.0,id=drive-sata-disk0,bootindex=1",
        ]
    else:
        qemu += [
            "-drive", f"file={overlay},if={profile['disk_if']},cache=writeback",
        ]
    qemu += qemu_netargs
    # Vendors whose first-boot bootloader prints to VGA (not serial) need
    # a VNC display so a human can actually see what's happening. nexus9000v
    # is the prime offender — SeaBIOS + GRUB output go to VGA only. We
    # expose VNC inside the wrapper on :0 (TCP 5900); the topology can
    # publish that port to the host for a Nexus node.
    if VENDOR == "nexus":
        qemu += ["-vga", "std", "-vnc", "0.0.0.0:0"]
    else:
        qemu += ["-nographic"]

    # Persist the QEMU command for vm-start.sh / vm-stop.sh — they're the
    # entry points the ztp-app uses (via docker exec) to control VM
    # lifecycle. Bridges + overlay are already set up; the helper just
    # has to spawn this command line.
    QEMU_CMD_FILE.write_text(
        "#!/bin/sh\n"
        f"# autogenerated by launch.py for {NODE_NAME}; do not edit\n"
        "exec " + " ".join(shlex.quote(a) for a in qemu) + "\n"
    )
    QEMU_CMD_FILE.chmod(0o755)
    print(f"[{NODE_NAME}] qemu command written to {QEMU_CMD_FILE}", flush=True)

    if VM_AUTOSTART:
        print(f"[{NODE_NAME}] VM_AUTOSTART=true, starting QEMU now", flush=True)
        subprocess.Popen(["/usr/local/bin/vm-start.sh"])
    else:
        print(f"[{NODE_NAME}] VM_AUTOSTART=false; waiting for "
              f"POST /api/devices/{NODE_NAME}/start", flush=True)

    # Persistent VM-console capture. Maintains ONE long-lived TCP
    # connection to QEMU's telnet:5000 (server,nowait — single client at
    # a time) and appends bytes to /tmp/qemu-console.log. The ZTP app's
    # SSE console endpoint just tails the file, so any number of
    # browsers can watch without poking QEMU's chardev (which gets stuck
    # if multiple direct clients churn).
    def _console_capture():
        log_path = Path("/tmp/qemu-console.log")
        log_path.touch()
        import socket as _socket
        while True:
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect(("127.0.0.1", 5000))
                s.settimeout(None)
                with log_path.open("ab", buffering=0) as f:
                    while True:
                        d = s.recv(4096)
                        if not d:
                            break
                        f.write(d)
                s.close()
            except Exception:
                pass
            time.sleep(2)
    import threading as _t
    _t.Thread(target=_console_capture, daemon=True).start()

    # Stay alive as PID 1 (under tini) so the container keeps running.
    # tini reaps any orphaned QEMU children; we just sleep until SIGTERM.
    def _sigterm(sig, frame):
        print(f"[{NODE_NAME}] caught signal {sig}, exiting", flush=True)
        # best effort: stop a running VM cleanly
        subprocess.run(["/usr/local/bin/vm-stop.sh"], check=False)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
