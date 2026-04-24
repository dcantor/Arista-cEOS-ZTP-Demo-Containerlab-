#!/bin/bash
# Universal ZTP bootstrap. cEOS downloads this via DHCP option 67 and runs
# it. We discover our identity from the systemd init environment (set by
# containerlab as CLAB_LABEL_CLAB_NODE_NAME) and fetch the matching config.
set -eu

SRV=http://172.30.0.20

HOST=$(tr '\0' '\n' < /proc/1/environ | awk -F= '/^CLAB_LABEL_CLAB_NODE_NAME=/{print $2}')
if [ -z "$HOST" ]; then
    HOST=unknown
fi

curl -fsS -X POST "$SRV/log?host=$HOST&event=start" || true
curl -fsS "$SRV/configs/$HOST.cfg" -o /mnt/flash/startup-config
sync
curl -fsS -X POST "$SRV/log?host=$HOST&event=done" || true
exit 0
