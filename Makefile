LAB := ztp-universal-demo
TOPO := topology.clab.yml
CEOS := spine1 spine2 leaf1 leaf2
APP_URL := http://localhost:8080

.PHONY: help build build-dnsmasq build-app build-veos \
        deploy deploy-with-vms destroy redeploy \
        start-vms stop-vms \
        ps dhcp-logs app-logs ztp-events ui-dev \
        console-spine1 console-spine2 console-leaf1 console-leaf2 console-leaf101

help:
	@echo "Targets:"
	@echo "  build           Build all three Docker images (dnsmasq + ztp-app + veos-launcher)"
	@echo "  build-app       Build only the ztp-app image (UI + FastAPI backend)"
	@echo "  build-dnsmasq   Build only the dnsmasq image"
	@echo "  build-veos      Build only the veos-launcher image (QEMU wrapper)"
	@echo "  deploy          Bring up the lab — servers + 4 vEOS wrappers, VMs STOPPED."
	@echo "                  UI on http://<host>:8080. Click Start per device or run"
	@echo "                  'make start-vms' to boot all four."
	@echo "  deploy-with-vms deploy + immediately start all 4 vEOS VMs (old behavior)"
	@echo "  start-vms       Start every vEOS VM via the app API (POST /api/devices/<n>/start)"
	@echo "  stop-vms        Stop every vEOS VM via the app API"
	@echo "  destroy         Tear down the lab"
	@echo "  redeploy        Destroy then deploy (only way to re-run ZTP — see Limitations)"
	@echo "  ps              Show lab containers and addresses"
	@echo "  dhcp-logs       Tail dnsmasq logs (DHCP exchanges)"
	@echo "  app-logs        Tail ztp-app (FastAPI) logs"
	@echo "  ztp-events      Show only the ZTP /log POSTs"
	@echo "  ui-dev          Run UI dev server (vite) against the deployed lab's API"
	@echo "  console-<node>  Open vEOS serial console over telnet (console-spine1 ...)"

build: build-dnsmasq build-app build-veos

build-dnsmasq:
	sudo docker build -t dnsmasq-ztp:latest ztp-server/dnsmasq/

build-app:
	sudo docker build -t ztp-app:latest ztp-server/app/

build-veos:
	sudo docker build -t veos-launcher:latest veos-launcher/

deploy: build
	sudo containerlab deploy -t $(TOPO)
	@echo ""
	@echo "  UI ready: http://$$(hostname -I | awk '{print $$1}'):8080"
	@echo "  vEOS VMs are STOPPED — click Start per device in the UI or run"
	@echo "  'make start-vms' to boot all four (3-5 min boot + ZTP per node)."
	@echo ""

deploy-with-vms: deploy start-vms

# Bulk control of every vEOS VM in the lab via the app's REST API.
# Each per-node call is idempotent server-side, so re-running these is safe.
start-vms:
	@for n in $(CEOS); do \
	  echo "==> POST $(APP_URL)/api/devices/$$n/start"; \
	  curl -fsS -X POST $(APP_URL)/api/devices/$$n/start | python3 -m json.tool || exit 1; \
	done

stop-vms:
	@for n in $(CEOS); do \
	  echo "==> POST $(APP_URL)/api/devices/$$n/stop"; \
	  curl -fsS -X POST $(APP_URL)/api/devices/$$n/stop | python3 -m json.tool || exit 1; \
	done

destroy:
	sudo containerlab destroy -t $(TOPO) --cleanup
	# Wipe vEOS overlay disks so a fresh deploy re-runs ZTP from scratch.
	sudo rm -rf data/veos-overlay
	mkdir -p data/veos-overlay

redeploy: destroy deploy

ps:
	sudo containerlab inspect -t $(TOPO)

dhcp-logs:
	sudo docker logs -f clab-$(LAB)-ztp-dhcp

app-logs:
	sudo docker logs -f clab-$(LAB)-ztp-http

ztp-events:
	sudo docker logs clab-$(LAB)-ztp-http 2>&1 | grep '/log?'

ui-dev:
	cd ztp-server/app/ui && npm install && npm run dev

# Console = QEMU's serial on TCP:5000 inside the wrapper's netns. The
# wrapper container's eth0 has no IP (the launcher strips it so the VM's
# mgmt interface can own the L3 address), so we reach the console by
# telnetting from inside the container itself. Use ctrl-] then 'quit' to
# exit. The console only listens while the VM is running — start it via
# the UI or `curl -X POST .../api/devices/<n>/start` first.
# Cred hints: arista nodes login admin/admin; leaf101 (cisco) login admin/cisco.
define console_target
	@if ! sudo docker exec clab-$(LAB)-$(1) /usr/local/bin/vm-status.sh 2>/dev/null | grep -q running; then \
	  echo "VM '$(1)' is not running. Start it first:"; \
	  echo "  curl -X POST $(APP_URL)/api/devices/$(1)/start"; \
	  exit 1; \
	fi
	@echo "Console for $(1) (ctrl-] then 'quit' to exit)"
	@sudo docker exec -it clab-$(LAB)-$(1) telnet localhost 5000
endef

console-spine1:  ; $(call console_target,spine1)
console-spine2:  ; $(call console_target,spine2)
console-leaf1:   ; $(call console_target,leaf1)
console-leaf2:   ; $(call console_target,leaf2)
console-leaf101: ; $(call console_target,leaf101)
