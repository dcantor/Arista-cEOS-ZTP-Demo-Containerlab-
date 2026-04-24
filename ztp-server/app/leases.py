from pathlib import Path

LEASES_FILE = Path("/dhcp-state/dnsmasq.leases")
RANGE_START = "172.30.0.100"
RANGE_END = "172.30.0.200"


def _ip_to_int(ip: str) -> int:
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def parse() -> list[dict]:
    """dnsmasq lease format (one per line):
        <expiry-epoch> <mac> <ip> <hostname-or-*> <client-id-or-*>
    """
    if not LEASES_FILE.exists():
        return []
    out = []
    for line in LEASES_FILE.read_text().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        expiry, mac, ip, hostname = parts[:4]
        out.append({
            "mac": mac,
            "ip": ip,
            "hostname": None if hostname == "*" else hostname,
            "expiry_epoch": int(expiry),
        })
    return sorted(out, key=lambda r: _ip_to_int(r["ip"]))


def pool_summary() -> dict:
    leases = parse()
    used = len(leases)
    total = _ip_to_int(RANGE_END) - _ip_to_int(RANGE_START) + 1
    return {
        "range_start": RANGE_START,
        "range_end": RANGE_END,
        "total": total,
        "used": used,
        "free": total - used,
        "leases": leases,
    }
