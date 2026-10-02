# Dual-stack Gateway, TLS и stable/canary

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
и runtime security, затем здоровый canary и оба демонстрационных отказа
с заголовками release/revision. Kubernetes/Gateway приемка выполняется отдельно.

## Расширение FINAL-01

FINAL-01 добавляет HTTPS `8443 → 443 → NodePort 30443` и второй backend.
HTTP остается на `8000 → 80 → NodePort 30080`, без redirect. Значения
`node_port`, `https_node_port`, `tls_hostname` берет общий deploy из выбранного
environment; по умолчанию это `30080`, `30443`, `hello.test`.

| Helm release | Ресурсы | Track | Replicas |
| --- | --- | --- | --- |
| `app` | `hello` | `stable` | 2 |
| `app-canary` | `hello-canary` | `canary` | 1, передается общим deploy |

Оба release используют `deploy/app` с разными `fullnameOverride` и
`releaseTrack`. Service selectors включают `app.kubernetes.io/instance`,
поэтому canary не попадает в stable Service. Stable обновляется с
`maxUnavailable: 0`, `maxSurge: 1`. Оба нормальных ответа имеют ровно
12 bytes `Hello World!`, без перевода строки. Nginx всегда возвращает
`X-Release` и `X-Revision`; JSON access получает `release` и `revision`.
Значение `releaseRevision` ограничено 64 безопасными символами. Это поля
записи, а не новые Loki labels. Fluentd glob `hello-*` охватывает также
pod `hello-canary-*`.

Canary использует `Recreate`: обычное изменение кандидата выполняется
после изоляции общих маршрутов на 100/0, старый candidate pod завершается
до запуска нового. Так smoke не чередует ответы двух ревизий кандидата.
Stable сохраняет RollingUpdate и две replicas.

Перед остановкой Nginx pod выполняет `preStop: sleep 5` в пределах
`terminationGracePeriodSeconds: 30`. На это время Nginx продолжает
отвечать, пока удаление endpoint распространяется до Traefik. Это
сокращает окно ошибок при rollout и удалении одного stable pod;
отсутствие ошибок подтверждает отдельный непрерывный live-тест FINAL-05.
Ожидание нового canary по revision и отсутствие retries сохраняются.

Для демонстрации отказов `faultMode: http500` возвращает 500 на `/` и
`/verify`, `faultMode: wrong-body` меняет тело на `Broken World!`.
`/healthz` остается здоровым, чтобы gate проверял готовый, но ошибочный
кандидат. Schema разрешает эти режимы только для `releaseTrack: canary`.
`responseBody` по умолчанию `Hello World!`; изменение значения требует
согласования ожидаемого тела в проверках.

Все четыре HTTPRoute принадлежат единственному Helm release `gateway`.
Каждый явно подключен к `web` и `websecure`:

| HTTPRoute | Match | Backend |
| --- | --- | --- |
| `hello` | Exact `/`, PathPrefix `/verify`, любой HTTP host | `hello` / `hello-canary` с общими весами |
| `hello-host` | Те же пути, hostname `hello.test` | Те же веса |
| `hello-stable` | Exact `/backend/stable` | Только `hello:80`, weight 100 |
| `hello-canary` | Exact `/backend/canary` | Только `hello-canary:80`, weight 100 |

Тестовые пути используют стандартный Gateway API `URLRewrite` в `/`.
Кандидат доступен для smoke при нулевом весе. Эти пути не входят в
выборку распределения общего трафика. `ResponseHeaderModifier` добавляет
`X-Gateway-Route: hello|hello-host` для проверки hostname-маршрутизации.
Оба пользовательских маршрута также добавляют `X-Traffic-Split: 100/0`
или `90/10` из тех же values, что и backendRefs. Общий release helper
ждет этот маркер фактически обслуживаемой конфигурации после изменения
весов, затем начинает строгую выборку без retries. Одних текущих API
conditions недостаточно: Traefik может еще обслуживать прежнюю конфигурацию.
FINAL checker требует ожидаемый маркер на IP/hostname запросах, сохраняя
проверки фактического распределения и release/revision. Детерминированные
`/backend/<track>` от общих весов не зависят и этот маркер не добавляют.
Незнакомый HTTP host сохраняет поведение `hello`; `/backend/canary/extra`
и `/unrouted` дают 404.

`traffic.stableWeight` / `traffic.canaryWeight` по умолчанию `100/0`;
каждый вес — целое число 0–100, сумма строго 100. Оба пользовательских
маршрута меняются одним Helm release. Общий release helper интегратора
владеет весами; checker их не меняет.

TLS завершается на HTTPS listener с `tls.mode: Terminate`. Secret
`hello-tls` содержит `tls.crt`, `tls.key` и публичный `ca.crt`. Chart
создает его только при передаче всех трех values: `tls.certificate`,
`tls.privateKey`, `tls.caCertificate`; частичный набор останавливает
render. Общий deploy отвечает за постоянные локальные сертификаты,
передачу временным values-файлом и исключение ключей из render artifacts.
CA для приемки: `.state/tls/<ENV>/ca.crt`.

Hostname задается в `hello-host`, а HTTPS listener принимает все имена.
Это допустимо по [Gateway API 1.4](https://github.com/kubernetes-sigs/gateway-api/blob/v1.4.0/apis/v1/gateway_types.go#L349-L398).
В Traefik 3.6.2 ограничение hostname на listener переносится в hostless
route через [`findMatchingHostnames`](https://github.com/traefik/traefik/blob/v3.6.2/pkg/provider/kubernetes/gateway/kubernetes.go#L974-L995).
Тогда `hello` и `hello-host` получают одинаковые Host rules и приоритеты.
Без ограничения listener только `hello-host` получает дополнительную
[специфичность имени](https://github.com/traefik/traefik/blob/v3.6.2/pkg/provider/kubernetes/gateway/httproute.go#L628-L636).
TLS Secret и проверка имени сертификата остаются обязательными;
приемка по HTTPS требует именно `X-Gateway-Route: hello-host`.

Чтение и проверка существующей конфигурации:

```sh
.venv/bin/python scripts/check-gateway.final.py --env ubuntu \
  --output artifacts/gateway-final-100-0.json
# Только после установки 90/10 общим release helper:
.venv/bin/python scripts/check-gateway.final.py --env ubuntu --split \
  --output artifacts/gateway-final-90-10.json
```

Checker использует только kubeconfig выбранного environment, сверяет
generation/conditions обеих listeners и всех routes, точные backend refs,
веса, host/path и TLS Secret reference. HTTP и HTTPS проверяются по обеим
IP-семьям. Curl использует `--resolve` и `--cacert`, без proxy, redirects,
retries или `-k`. Отрицательные TLS-пробы требуют отказа для другого имени
и другого CA.

В закрепленном Traefik 3.6.2 `attachedRoutes` равен **8 для каждой listener**
при наших четырех HTTPRoute с двумя явными parentRefs. Причина:
[`matchingGatewayListeners`](https://github.com/traefik/traefik/blob/v3.6.2/pkg/provider/kubernetes/gateway/kubernetes.go#L1035-L1060)
добавляет listener за каждую ссылку на Gateway без учета sectionName;
[`loadHTTPRoutes`](https://github.com/traefik/traefik/blob/v3.6.2/pkg/provider/kubernetes/gateway/httproute.go#L35-L67)
увеличивает счетчик при каждом принятом повторе. Это особенность счетчика,
а не восемь самостоятельных routes. Checker требует ровно `4 × 2 = 8`,
отдельно проверяет ровно четыре ожидаемых имени routes и по два актуальных
parent status. Смена закрепленной версии требует повторно проверить эту
семантику; любое другое значение счетчика сейчас дает fail.

По умолчанию для каждой семьи выполняются 1000 независимых GET `/`
через HTTP NodePort. При `--split` допустимы 5–15% canary; любой неверный
HTTP status/body/Request-ID/release/revision, транспортная ошибка или
неполная выборка дают fail. В режиме 100/0 canary должен отсутствовать.
Условия и веса проверяются повторно после выборки. Общий deadline —
300 секунд, настраивается `--timeout`; report не сохраняет IP, URL и
ключи. Приемка обоих режимов не заменяет возврат 100/0 интегратором.

Дополнительные локальные render/fixture проверки:

```sh
.venv/bin/python deploy/app/tests/test_render.py
.venv/bin/python deploy/gateway/tests/test_render.py
.venv/bin/python deploy/gateway/tests/test_final.py
```

Первичные источники: [Traefik 3.6 Gateway API](https://doc.traefik.io/traefik/v3.6/reference/routing-configuration/kubernetes/gateway-api/),
[Gateway API URLRewrite](https://gateway-api.sigs.k8s.io/guides/user-guides/http-redirect-rewrite/),
[Gateway API TLS](https://gateway-api.sigs.k8s.io/guides/user-guides/tls/).
