#!/usr/bin/env python
"""
Cisco NX-OS Power-On Auto Provisioning (POAP) script for leaf201
(Nexus 9300v lite).

POAP runs on first boot when there's no startup-config in NVRAM. The
NX-OS bootloader reads DHCP option 67 from the dnsmasq response, fetches
this script from the ZTP app, and runs it under NX-OS's Python (the cli
module is pre-imported).

Flow: POST event=start, copy the per-host config from the ZTP server
into bootflash, copy that into startup-config, POST event=done. NX-OS
reboots automatically once POAP exits cleanly with a good
startup-config in place.

Note: NX-OS uses a `vrf management` for mgmt-interface traffic, so the
copy commands must specify it.
"""
import os
import sys

try:
    # Available inside NX-OS Python
    from cli import cli  # type: ignore
except ImportError:
    cli = None  # for off-device lint/test

try:
    from urllib.request import urlopen, Request
except ImportError:
    from urllib2 import urlopen, Request  # type: ignore

HOST = "leaf201"
SRV  = "http://172.30.0.20"


def http_post(url):
    try:
        urlopen(Request(url, data=b""), timeout=10).read()
    except Exception:
        pass  # logging is best-effort


def main():
    print("POAP: starting for {}".format(HOST))
    http_post("{}/log?host={}&event=start".format(SRV, HOST))

    if cli is None:
        print("POAP: cli module not present (off-device dry-run), skipping apply")
        return

    cfg_url = "{}/configs/{}.cfg".format(SRV, HOST)
    local_path = "bootflash:poap_replay.cfg"
    print("POAP: copying {} -> {}".format(cfg_url, local_path))
    cli("copy {} {} vrf management".format(cfg_url, local_path))
    print("POAP: copying {} -> startup-config".format(local_path))
    cli("copy {} startup-config".format(local_path))

    http_post("{}/log?host={}&event=done".format(SRV, HOST))
    print("POAP: done; NX-OS will reboot into the new startup-config")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("POAP: fatal error: {}".format(e))
        sys.exit(1)
