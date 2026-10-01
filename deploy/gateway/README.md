# IPv6 Gateway MVP

Traefik обслуживает HTTP NodePort по IPv6; `hello` и Traefik Service имеют
`SingleStack` / `ipFamilies: [IPv6]`. Nginx слушает только
`[::]:8080 ipv6only=on`; backend-трафик идет на IPv6 Pod через Service.
Gateway status получает IPv6 node address от общей установки.

Upstream Traefik chart 37.4.0 не умеет корректно подставить IPv6 `hostIP`
в CLI address: этот же параметр попадает в Kubernetes container port.
Поэтому общий post-renderer заменяет wildcard entrypoint addresses
на `[::]:PORT/tcp` для web, metrics и health. Metrics Service остается
внутренним IPv6 ClusterIP.

`check-gateway.py` требует native IPv6 Gateway status и IPv6 literal URL,
например `--base-url 'http://[2001:db8::10]:30080'`. IPv4 и IPv4-mapped
IPv6 не проходят проверку. Адрес здесь из документационного диапазона;
реальный передается только в локальном environment.

```sh
python3 scripts/check-gateway.test.py
python3 scripts/check-gateway.container.py --image "$LOCKED_NGINX_IMAGE"
```

Первый тест включает настоящий HTTP на `::1` с `IPV6_V6ONLY=1` и fixtures
условий Gateway/ошибок HTTP. Docker smoke создает уникальную одноразовую
IPv6-only сеть (`--ipv6 --ipv4=false`) и публикует порт только на `[::1]`.
Нужны IPv6 на хосте и Docker с этими флагами (проверен 29.8.0). Smoke
проверяет реальный Nginx, Request-ID, stdout/stderr и runtime security;
Kubernetes/Gateway приемку выполняет интегратор отдельно.
