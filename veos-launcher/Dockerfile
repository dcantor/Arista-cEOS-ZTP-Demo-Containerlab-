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
RUN chmod +x /launch.py /healthcheck.sh

# vEOS console
EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=20 \
  CMD ["/healthcheck.sh"]

ENTRYPOINT ["/usr/bin/tini", "--", "/launch.py"]
