#!/bin/bash
HOST=spine2
SRV=http://172.30.0.20

curl -fsS -X POST "$SRV/log?host=$HOST&event=start" || true
curl -fsS "$SRV/configs/$HOST.cfg" -o /mnt/flash/startup-config
sync
curl -fsS -X POST "$SRV/log?host=$HOST&event=done" || true
exit 0
