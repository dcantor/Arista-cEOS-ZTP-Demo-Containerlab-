LAB := ztp-universal-demo
TOPO := topology.clab.yml

.PHONY: help build build-dnsmasq build-app build-veos deploy destroy redeploy ps dhcp-logs app-logs ztp-events ui-dev console-spine1 console-spine2 console-leaf1 console-leaf2

help:
	@echo "Targets:"
	@echo "  build         Build all three Docker images (dnsmasq + ztp-app + veos-launcher)"
	@echo "  build-app     Build only the ztp-app image (UI + FastAPI backend)"
	@echo "  build-dnsmasq Build only the dnsmasq image"
	@echo "  build-veos    Build only the veos-launcher image (QEMU wrapper)"
	@echo "  deploy        Bring up the ZTP lab (servers + 4 vEOS VMs)"
	@echo "                UI on http://<host>:8080"
	@echo "  destroy       Tear down the lab"
	@echo "  redeploy      Destroy then deploy (everything)"
	@echo "  ps            Show lab containers and addresses"
	@echo "  dhcp-logs     Tail dnsmasq logs (DHCP exchanges)"
	@echo "  app-logs      Tail ztp-app (FastAPI) logs"
	@echo "  ztp-events    Show only the ZTP /log POSTs"
	@echo "  ui-dev        Run UI dev server (vite) against the deployed lab's API"
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
	@echo "  (vEOS VMs take ~3-5 min each to boot and complete ZTP)"
	@echo ""

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

# vEOS has no docker exec Cli; console is the qemu serial on TCP:5000.
# Use ctrl-] then 'quit' to exit telnet.
console-spine1:
	@IP=$$(sudo docker inspect clab-$(LAB)-spine1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); \
	 echo "Connecting to spine1 console at $$IP:5000 (ctrl-] then 'quit' to exit)"; \
	 telnet $$IP 5000

console-spine2:
	@IP=$$(sudo docker inspect clab-$(LAB)-spine2 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); \
	 telnet $$IP 5000

console-leaf1:
	@IP=$$(sudo docker inspect clab-$(LAB)-leaf1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); \
	 telnet $$IP 5000

console-leaf2:
	@IP=$$(sudo docker inspect clab-$(LAB)-leaf2 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'); \
	 telnet $$IP 5000
