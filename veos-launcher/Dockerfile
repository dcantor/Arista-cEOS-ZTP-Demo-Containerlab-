FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qy \
 && apt-get install -y --no-install-recommends \
      qemu-kvm qemu-utils \
      bridge-utils iproute2 iputils-ping \
      python3 python3-yaml \
      socat telnet ca-certificates \
      tini \
 && rm -rf /var/lib/apt/lists/*

# vEOS qcow2 is bind-mounted at runtime to avoid a multi-GB image rebuild.
# (See topology.clab.yml binds; expected at /vEOS.qcow2)

COPY launch.py /launch.py
COPY healthcheck.sh /healthcheck.sh
COPY vm-start.sh vm-stop.sh vm-status.sh /usr/local/bin/
RUN chmod +x /launch.py /healthcheck.sh /usr/local/bin/vm-*.sh

# vEOS console
EXPOSE 5000

# Healthy = launcher finished bridge setup (qemu-cmd.sh exists). Whether
# the VM itself is currently running is independent and exposed via the
# /api/devices/<host>/status endpoint, not as Docker health.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
  CMD ["/healthcheck.sh"]

ENTRYPOINT ["/usr/bin/tini", "--", "/launch.py"]
