"""
IOS-XE Zero Touch Provisioning script for leaf101 (CSR1000v).

Cisco's ZTP runs Python scripts in the IOS-XE guest shell. This file is
served by the ZTP app at /ztp/leaf101.py and pointed at by dnsmasq
Option 67. The flow:

  1. CSR1000v boots with no startup-config -> enters ZTP
  2. DHCPs on Gi1, gets Option 67 = http://172.30.0.20/ztp/leaf101.py
  3. IOS-XE downloads + runs this script
  4. We POST event=start, fetch the IOS config, apply it via cli, save,
     POST event=done

Reference: https://blogs.cisco.com/learning/cisco-ios-xe-zero-touch-provisioning
"""
import sys
import time

try:
    import cli  # IOS-XE built-in
except ImportError:
    cli = None  # allows local dev/lint; never None on a real device

# Python 3 first (IOS-XE 17.x), fall back to 2 just in case.
try:
    from urllib.request import urlopen, Request
except ImportError:
    from urllib2 import urlopen, Request  # type: ignore

HOST = "leaf101"
SRV  = "http://172.30.0.20"


def http_get(url, timeout=30):
    return urlopen(Request(url), timeout=timeout).read().decode("utf-8")


def http_post(url, timeout=10):
    try:
        urlopen(Request(url, data=b""), timeout=timeout).read()
    except Exception:
        pass  # best-effort logging


def main():
    print("ZTP: starting for {}".format(HOST))
    http_post("{}/log?host={}&event=start".format(SRV, HOST))

    cfg_url = "{}/configs/{}.cfg".format(SRV, HOST)
    print("ZTP: fetching {}".format(cfg_url))
    config = http_get(cfg_url)

    # Persist the rendered config to bootflash so we can inspect it later.
    with open("/bootflash/ztp-applied.cfg", "w") as f:
        f.write(config)

    # Apply via the IOS-XE Python CLI module. cli.configure() takes a
    # config payload as a single string.
    if cli is None:
        print("ZTP: cli module not present (running off-device?), skipping apply")
        return
    print("ZTP: applying config")
    cli.configure(config)
    cli.executep("write memory")

    # Tiny pause so 'write memory' completes before our POST returns.
    time.sleep(1)
    http_post("{}/log?host={}&event=done".format(SRV, HOST))
    print("ZTP: done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ZTP: fatal error: {}".format(e))
        sys.exit(1)
