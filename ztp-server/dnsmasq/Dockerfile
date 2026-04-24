FROM alpine:3.20
RUN apk add --no-cache dnsmasq
ENTRYPOINT ["dnsmasq", "-k", "--log-facility=-", "--conf-file=/etc/dnsmasq.conf"]
