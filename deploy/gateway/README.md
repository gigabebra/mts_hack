# Dual-stack Gateway MVP

Команды ниже выполняются из корня checkout после `make tools`. Для прямого
вызова закрепленных Helm/Ansible/Python подготовьте PATH в этом терминале:

```sh
export PATH="$PWD/.cache/tools:$PWD/.venv/bin:$PATH"
```

Traefik обслуживает HTTP NodePort по IPv6 и IPv4. `hello` и Traefik
Services имеют `RequireDualStack` / `ipFamilies: [IPv6, IPv4]`.
Nginx слушает `0.0.0.0:8080` и `[::]:8080 ipv6only=on`; приложение
принимает обе семьи. Gateway status объявляет основной IPv6 node address.

Upstream Traefik chart 37.4.0 не умеет корректно подставить IPv6 `hostIP`
в CLI address: этот же параметр попадает в Kubernetes container port.
Общий post-renderer задает wildcard `[::]:PORT/tcp` для web, metrics и
health. Go listener на Linux принимает обе семьи. Metrics Service остается
внутренним dual-stack ClusterIP. `global.checkNewVersion` и
`global.sendAnonymousUsage` выключены, чтобы не запускать необязательные
внешние проверки и отчеты.

`check-gateway.py` требует native IPv6 unicast Gateway status. Параметр
`--expected-address` дополнительно сравнивает status с точным `node_ip`
выбранного environment; общий `make verify` передает его автоматически.
Без этого параметра standalone checker проверяет семейство и допустимость
адреса, но не его принадлежность конкретному стенду. HTTP origin
принимает native IPv6 или IPv4, например
`--base-url 'http://[2001:db8::10]:30080'` и
`--base-url 'http://192.0.2.10:30080'`. IPv4-mapped и scoped IPv6 URL
отклоняются; реальные адреса хранятся только в локальном environment.
HTTP и kubectl обращаются к указанному стенду без унаследованных proxies.

```sh
python3 scripts/check-gateway.test.py
.venv/bin/python deploy/gateway/tests/test_render.py
LOCKED_NGINX_IMAGE="$(PYTHONPATH=scripts .venv/bin/python -c \
  'from stacklib import IMAGES, VERSIONS; ref = VERSIONS["images"]["nginx"]; print(ref + "@" + IMAGES[ref])')"
python3 scripts/check-gateway.container.py --image "$LOCKED_NGINX_IMAGE"
```

Fixtures проверяют настоящий HTTP на `::1`, условия Gateway, IPv4 HTTP
origin и ошибки HTTP/proxy environment. Render test подтверждает
эффективные настройки telemetry/version checks. Docker smoke создает
уникальную одноразовую dual-stack сеть и публикует порты только на `::1`
и `127.0.0.1`. Нужен IPv6 на Docker host (проверен Docker 29.8.0).
Smoke проверяет реальный Nginx по обеим семьям, Request-ID, stdout/stderr
и runtime security. Kubernetes/Gateway приемка выполняется отдельно.
