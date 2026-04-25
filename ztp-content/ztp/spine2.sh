#!/bin/bash
HOST=spine2
SRV=http://172.30.0.20

curl -fsS -X POST "$SRV/log?host=$HOST&event=start" || true

# Optional EOS image upgrade. The /ztp/eos-image/<host> endpoint returns
# either an empty body (skip) or just the .swi filename.
IMAGE=$(curl -fsS "$SRV/ztp/eos-image/$HOST" 2>/dev/null)
if [ -n "$IMAGE" ]; then
    curl -fsS -X POST "$SRV/log?host=$HOST&event=image-download" || true
    curl -fsS "$SRV/eos-images/$IMAGE" -o "/mnt/flash/$IMAGE"
    # Point boot-config at the new image so the post-ZTP reboot uses it.
    echo "SWI=flash:$IMAGE" > /mnt/flash/boot-config
fi

curl -fsS "$SRV/configs/$HOST.cfg" -o /mnt/flash/startup-config
sync
curl -fsS -X POST "$SRV/log?host=$HOST&event=done" || true
exit 0
