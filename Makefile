LAB := ztp-universal-demo
TOPO := topology.clab.yml

.PHONY: help build build-dnsmasq build-app deploy destroy redeploy ps dhcp-logs app-logs ztp-events ui-dev cli-spine1 cli-spine2 cli-leaf1 cli-leaf2

help:
	@echo "Targets:"
	@echo "  build         Build both Docker images (dnsmasq + ztp-app)"
	@echo "  build-app     Build only the ztp-app image (UI + FastAPI backend)"
	@echo "  build-dnsmasq Build only the dnsmasq image"
	@echo "  deploy        Bring up the ZTP lab (servers + 4 cEOS); UI on http://<host>:8080"
	@echo "  destroy       Tear down the lab"
	@echo "  redeploy      Destroy then deploy (only way to re-run ZTP — see Limitations)"
	@echo "  ps            Show lab containers and addresses"
	@echo "  dhcp-logs     Tail dnsmasq logs (DHCP exchanges)"
	@echo "  app-logs      Tail ztp-app (FastAPI) logs"
	@echo "  ztp-events    Show only the ZTP /log POSTs"
	@echo "  ui-dev        Run UI dev server (vite) against the deployed lab's API"
	@echo "  cli-<node>    Open Arista CLI on a node (cli-spine1, cli-leaf2, ...)"

build: build-dnsmasq build-app

build-dnsmasq:
	sudo docker build -t dnsmasq-ztp:latest ztp-server/dnsmasq/

build-app:
	sudo docker build -t ztp-app:latest ztp-server/app/

deploy: build
	sudo containerlab deploy -t $(TOPO)
	@echo ""
	@echo "  UI ready: http://$$(hostname -I | awk '{print $$1}'):8080"
	@echo ""

destroy:
	sudo containerlab destroy -t $(TOPO) --cleanup

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

cli-spine1:
	sudo docker exec -it clab-$(LAB)-spine1 Cli -p 15

cli-spine2:
	sudo docker exec -it clab-$(LAB)-spine2 Cli -p 15

cli-leaf1:
	sudo docker exec -it clab-$(LAB)-leaf1 Cli -p 15

cli-leaf2:
	sudo docker exec -it clab-$(LAB)-leaf2 Cli -p 15
