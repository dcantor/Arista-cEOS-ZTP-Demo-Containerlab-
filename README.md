# Arista vEOS ZTP Demo (Containerlab)

End-to-end Zero Touch Provisioning lab for Arista **vEOS** (the full
QEMU/KVM virtual machine), with a built-in web dashboard. Four switches
(2 spines + 2 leaves) boot from a fresh qcow2 overlay with **no
startup-config**, broadcast DHCP on Management1, and pull per-device
configuration from the ZTP server. The whole stack — DHCP server,
ZTP/UI app, and the four vEOS VMs — comes up with a single
`containerlab deploy`.

> Earlier revisions of this lab used cEOS-lab containers. We migrated to
> vEOS to get a real, full-feature EOS image (no `reload now` block, no
> netns-tied lifecycle) at the cost of slower boot times (3–5 min per
> VM) and more RAM (~2 GB per node). See **History & limitations** below.

![Device inventory](docs/screenshots/devices.png)

## Architecture

```
       ┌────────────────────────────────────────────────────────┐
       │  ztp-mgmt bridge   172.30.0.0/24                       │
       │                                                        │
       │  ztp-dhcp (dnsmasq)         ztp-app (FastAPI + React)  │
       │   172.30.0.10                172.30.0.20  →  host:8080 │
       │      ▲                          ▲                      │
       │      │ DHCP DISCOVER/OFFER      │ HTTP GET / POST      │
       │      │                          │                      │
       │   spine1   spine2   leaf1   leaf2                      │
       │   (vEOS)   (vEOS)   (vEOS)  (vEOS)                     │
       └────────────────────────────────────────────────────────┘
                  │       │       │       │
                  └─── point-to-point fabric links ───┘
```

Each vEOS node runs inside a `veos-launcher` container that wraps
qemu-kvm. The launcher:

1. Waits for containerlab to attach `eth1`/`eth2` to the netns.
2. For each container interface, creates a Linux bridge with a tap and
   moves the interface into the bridge in promiscuous mode (so frames
   destined for the VM's MAC are accepted, not dropped at kernel
   ingress).
3. Builds QEMU args with one `e1000` NIC per tap, with deterministic
   MACs derived from the node name (so dnsmasq reservations are stable).
4. Boots vEOS with the project's qcow2 backed by a per-node overlay
   (`data/veos-overlay/<node>.qcow2`).

The serial console of every VM is exposed on TCP `:5000` of its wrapper
container. The Live ZTP Viewer in the UI streams it.

## ZTP flow per device

1. vEOS boots from its overlay with no `/mnt/flash/startup-config` →
   enters ZTP mode.
2. Sends `DHCPDISCOVER` on Management1 (which is bridged to the
   container's `eth0` on `ztp-mgmt`).
3. `dnsmasq` matches the deterministic MAC, replies with a reserved IP
   (`172.30.0.101–104`) and DHCP Option 67 bootfile URL
   `http://172.30.0.20/ztp/<host>.sh`.
4. vEOS downloads the per-host script and runs it. The script POSTs
   `event=start` to `/log`, fetches `/configs/<host>.cfg`, writes it to
   `/mnt/flash/startup-config`, then POSTs `event=done`.
5. vEOS detects ZTP success, reboots, and comes up with the new config.

## Web UI

After `make deploy`, browse to **`http://<lab-host-ip>:8080`** (the
Makefile prints the URL when the deploy finishes). The first deploy
takes 5–7 minutes for vEOS to boot + ZTP + reboot.

### Devices

Live inventory: container status, ZTP state pill, current MAC and
mgmt IP, last seen, event count. Updates push in via Server-Sent Events.
The **Source** column distinguishes `topology` (vEOS nodes from
`topology.clab.yml`) from `managed` (devices the user registered via
the UI — see below).

![Devices](docs/screenshots/devices.png)

The **+ Add device** button at the bottom of the grid registers an
external device by MAC + mgmt IP. Each add:

1. Inserts a row in `managed_devices` (SQLite).
2. Rewrites `/dhcp-state/managed.conf` with `dhcp-host=<mac>,<ip>,set:<name>`
   plus `dhcp-boot=tag:<name>,http://172.30.0.20/ztp/<name>.sh`.
3. Restarts the dnsmasq container (a SIGHUP would only reload leases,
   not the new `dhcp-host` entries from `conf-file=` includes).
4. Auto-creates `ztp-content/ztp/<name>.sh` and a placeholder
   `ztp-content/configs/<name>.cfg` so the device is immediately
   ZTP-ready when it DHCPs in.

The per-managed-device **Delete** button reverses this (drops the
reservation and removes the script; the config file is left in place).
Backed by `GET/POST/DELETE /api/managed-devices`.

#### Per-device EOS image (ZTP-time upgrade)

The **EOS image (ZTP)** column on the Devices grid is a dropdown of
every `.swi` file in the project's `eos_images/` directory plus a
`(skip upgrade)` option (the default).

When set, the next ZTP cycle on that device will:
1. download the chosen `.swi` from `GET /eos-images/<filename>`,
2. write `SWI=flash:<filename>` to `/mnt/flash/boot-config`,
3. write the per-host startup-config,
4. reboot — coming back up on the new image.

When set to `(skip upgrade)` (or unset), the bootstrap script leaves
the running image alone and only applies the config. The selection
persists in SQLite (`device_settings` table). Backed by
`GET /api/eos-images`, `GET/PUT /api/devices/<host>/eos-image`,
`GET /eos-images/<filename>` (raw download), and
`GET /ztp/eos-image/<host>` (plain-text endpoint the bootstrap script
curls to learn its target).

The **Live ZTP Viewer** button per row opens a drawer that streams
`docker logs -f` for the *wrapper container* (qemu launcher output);
for the **vEOS VM's** serial console, telnet to the container IP on
port 5000, or use `make console-spine1` etc.

![Live ZTP Viewer](docs/screenshots/live-ztp-viewer.png)

### Device detail

Per-device summary, full event timeline, and the served EOS config.
**Apply config (live)** pushes the served config into the device's
running and startup config via eAPI's `configure replace force` —
no VM reboot, no container restart.

![Device detail](docs/screenshots/device-detail.png)

### Configs / Editor

In-browser editing of `ztp-content/configs/<host>.cfg`. **Save and
apply** writes the file and pushes it via eAPI in one click.

![Configs](docs/screenshots/configs.png)
![Config editor](docs/screenshots/config-editor.png)

### DHCP pool

dnsmasq lease snapshot.

![DHCP pool](docs/screenshots/leases.png)

### Events

Chronological event log across all devices.

![Event log](docs/screenshots/events.png)

### API (Swagger)

Auto-generated at `/docs`; toolbar link `API ↗` opens it in a new tab.

![API Swagger](docs/screenshots/api-swagger.png)

## Node / IP / config map

| Node    | DHCP-assigned mgmt IP | Final mgmt IP   | Config                       |
|---------|-----------------------|-----------------|------------------------------|
| spine1  | dynamic (`.100–.200`) | `172.30.0.101`  | `ztp-content/configs/spine1.cfg` |
| spine2  | dynamic (`.100–.200`) | `172.30.0.102`  | `ztp-content/configs/spine2.cfg` |
| leaf1   | dynamic (`.100–.200`) | `172.30.0.103`  | `ztp-content/configs/leaf1.cfg`  |
| leaf2   | dynamic (`.100–.200`) | `172.30.0.104`  | `ztp-content/configs/leaf2.cfg`  |

Default credentials baked into the per-host configs: `admin / admin`
(plaintext, applied via `username admin secret admin`). The eAPI is on
**HTTPS:443** with a self-signed cert.

## Fabric

```
spine1 (AS 65001)        spine2 (AS 65002)
   eth1 ─ leaf1 eth1        eth1 ─ leaf1 eth2
   eth2 ─ leaf2 eth1        eth2 ─ leaf2 eth2
```

eBGP underlay, `/31` p2p links:

| Link              | Spine side | Leaf side |
|-------------------|------------|-----------|
| spine1 ↔ leaf1    | 10.0.1.0   | 10.0.1.1  |
| spine1 ↔ leaf2    | 10.0.2.0   | 10.0.2.1  |
| spine2 ↔ leaf1    | 10.0.3.0   | 10.0.3.1  |
| spine2 ↔ leaf2    | 10.0.4.0   | 10.0.4.1  |

Loopbacks: spine1 `10.255.0.1`, spine2 `10.255.0.2`, leaf1 `10.255.0.11`, leaf2 `10.255.0.12`.

## Quickstart

```bash
make deploy           # builds 3 images, then containerlab deploy
                      # prints UI URL when done; first ZTP cycle ~5-7 min
make app-logs         # tail FastAPI access log
make ztp-events       # filter to ZTP /log POSTs
make console-spine1   # vEOS serial console (telnet; ctrl-] then 'quit')
```

`make destroy` wipes `data/veos-overlay/*.qcow2`, so the next deploy
re-runs ZTP from scratch on every node.

## Lifecycle

| Goal                                                | Command / action                                       |
|-----------------------------------------------------|--------------------------------------------------------|
| Bring up the lab                                    | `make deploy`                                          |
| Tear down (also wipes vEOS overlays)                | `make destroy`                                         |
| Re-run ZTP from scratch                             | `make redeploy`                                        |
| Apply current per-host config to a live device      | UI → device → **Apply config (live)** (no reboot)      |
| Persist a device's current state across redeploy    | Use `containerlab destroy` directly (skip `make destroy`); overlays preserved |
| Rebuild only the app image                          | `make build-app`                                       |
| Rebuild only the launcher image                     | `make build-veos`                                      |
| vEOS serial console                                 | `make console-<node>` or `telnet <wrapper-ip> 5000`    |

## History & limitations

**Why vEOS not cEOS?** The earlier cEOS version of this demo had a hard
limit: cEOS-lab can't reload itself in-place. `reload now` is blocked
("not supported on this hardware platform"), and any container-level
restart tears the netns and kills the externally-created data-plane
veth pairs (in *both* endpoints, since veth pairs die together). vEOS
is a real EOS VM — it can reload, restart, and behave like real
hardware.

**vEOS tradeoffs:**
- Boot time: ~3–5 minutes per VM (vs ~30 s for cEOS).
- Memory: ~2 GB per VM (vs ~500 MB for cEOS).
- Requires `/dev/kvm` and `/dev/net/tun` on the host.
- Scaling beyond ~8 nodes wants serious host RAM/CPU.

**vEOS still has the cEOS netns problem at the wrapper level.**
`docker stop`/`restart` the wrapper container destroys the netns and the
data-plane veths. Don't restart vEOS wrappers in isolation; use
`make redeploy` for clean state, or `Apply config (live)` for non-
disruptive config changes.

**Why MAC reservations work this time.** The launcher derives every NIC's
MAC deterministically from the node name (sha256 of `<name>:<idx>`,
locally-administered bit set). Restarts produce the same MAC, so
`dnsmasq.conf`'s `dhcp-host=<mac>,<ip>,set:<host>` lines are stable.

## Layout

```
.
├── topology.clab.yml          # containerlab topology
├── Makefile                   # deploy / destroy / build / console helpers
├── vEOS-lab-4.36.0F.qcow2     # the vEOS image (bind-mounted into wrappers)
├── docs/screenshots/          # UI screenshots used in this README
├── veos-launcher/             # Docker wrapper that runs qemu-kvm + vEOS
│   ├── Dockerfile             # debian + qemu-kvm + bridge-utils + python3
│   ├── launch.py              # bridge setup, deterministic MACs, qemu launch
│   └── healthcheck.sh
├── ztp-server/
│   ├── dnsmasq/
│   │   ├── Dockerfile         # alpine + dnsmasq
│   │   └── dnsmasq.conf       # per-node MAC reservations; option 67 per host
│   └── app/                   # FastAPI + React
│       ├── Dockerfile         # multi-stage: vite build → python:3.12-slim
│       ├── requirements.txt   # fastapi, uvicorn, docker, httpx, pydantic
│       ├── main.py            # ZTP endpoints + REST API + SSE + SPA mount
│       ├── db.py              # SQLite event store
│       ├── leases.py          # dnsmasq lease parser
│       ├── docker_ctl.py      # Docker SDK + eAPI client (Apply config live)
│       └── ui/                # Vite + React + TS + Tailwind
├── ztp-content/               # bind-mounted into ztp-app at /ztp-content
│   ├── ztp/<host>.sh          # per-host bootstrap scripts (one per node)
│   └── configs/<host>.cfg     # final EOS startup-configs (editable in UI)
├── dhcp-state/                # dnsmasq leases (shared with ztp-app, read-only)
└── data/
    ├── ztp.db                 # SQLite event store
    └── veos-overlay/<node>.qcow2  # per-node copy-on-write overlay
```

## Troubleshooting

- **vEOS stuck at "ZTP retry"**: dnsmasq isn't seeing the DHCP request.
  Check `make dhcp-logs`. Most common cause: launcher didn't put the
  container's `eth0` in promiscuous mode (it does this automatically;
  check `docker exec clab-…-spine1 ip link show eth0` for `PROMISC`).
- **Mgmt IP not reachable after ZTP**: verify `Management1` (not
  `Management0`!) has the IP via `make console-spine1` →
  `show ip int brief`.
- **Apply config (live) returns 500 timeout**: vEOS eAPI sluggish under
  CPU contention; `apply_config` already runs in a worker thread with
  a 120 s timeout. If this still trips, check host CPU.
- **Conflicting bridge subnet**: this lab uses `172.30.0.0/24`,
  separate from the default `clab-mgmt` bridge on `172.20.1.0/24`.

## Regenerating the screenshots

```bash
sudo docker run --rm \
  --network ztp-mgmt \
  -v $(pwd)/docs/screenshots/capture.js:/usr/src/app/capture.js:ro \
  -v $(pwd)/docs/screenshots:/out \
  -w /usr/src/app --entrypoint node \
  zenika/alpine-chrome:with-puppeteer capture.js
```
