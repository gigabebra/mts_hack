# Логирование: Fluentd и Loki

При `network_mode: ipv4` установщик передает chart значение `networkMode: ipv4`:
Loki и Fluentd слушают `0.0.0.0`, Service Loki имеет `[IPv4]` / `SingleStack`.
Проверки доставки, буфера и восстановления работают через IPv4, включая
monitor Fluentd на `127.0.0.1`. Далее описан `dual-stack` по умолчанию.

Команды выполняются из корня репозитория после установки или `make tools`.
Для прямого вызова закрепленных Helm/Ansible/Python настройте PATH:

```bash
export PATH="$PWD/.cache/tools:$PWD/.venv/bin:$PATH"
```

Chart `logging` ставится в namespace `logging`. Скрипт deploy передает
`images.loki`, `images.fluentd`, `dataRoot`; общий post-renderer заменяет image
tags принятым digest lock. Loki требует заранее созданный PV `mts-loki`,
StorageClass `local-retain` и директорию данных с UID/GID 10001:10001.
Fluentd требует каталог `<dataRoot>/fluentd` root:root 0750, который создает
bootstrap. Произвольные host paths сам chart не создает.

Loki использует один Deployment, PVC `loki` 12Gi, TSDB/v13, filesystem,
WAL и compactor на одном volume. Retention 24h применяется compactor
асинхронно; это не жесткая дисковая квота. Удаление chunk после retention
отложено еще на 2h. Нужен контроль свободного места; резервирования и HA нет.
Service Loki имеет `RequireDualStack` / `ipFamilies: [IPv6, IPv4]`;
ClusterIP 3100 предназначен для внутренней сети, authentication отключена.
HTTP/gRPC используют обычный `tcp` и wildcard `::`, принимающий обе семьи
на Linux; single-process ring использует штатный `127.0.0.1`. Fluentd
обращается к `loki.logging.svc.cluster.local`, который в dual-stack сети
возвращает AAAA и A. Необязательный analytics reporting остается выключен.

Fluentd 1.19.0 с Loki plugin из закрепленного image запускается с
`-p /fluentd/plugins`. Читает только `hello-*_demo_nginx-*.log` из
`/var/log/containers`, с read-only mount `/var/log/pods` для CRI symlinks.
CRI время сохраняет наносекунды; JSON access-поля добавляются к исходному
`message`. Непарсящийся JSON, включая обычный текст stderr, сохраняется.
Признак CRI `P` сохраняется, но сборка многофрагментных сообщений не
реализована: проверяемые короткие записи приложения имеют `F`.

Labels только `app`, `namespace`, `container`, `stream`. Автоматическое
добавление Loki `service_name` отключено. Pod/source path, URI и Request-ID
остаются в теле записи. Collector не обращается к Kubernetes API, не имеет
RBAC и ServiceAccount token; root нужен для node log files/positions.
Root filesystem read-only, capabilities сняты, privilege escalation
запрещен. Monitor endpoint 24220 показывает состояние процесса collector.
Он слушает `bind "[::]"`: именно bracketed значение необходимо HTTP helper
Fluentd 1.19, который формирует URI из `bind`. `bind ::` дает invalid URI
при старте. Wildcard принимает IPv6 и IPv4: monitor доступен по
`http://[::1]:24220/api/plugins.json` и
`http://127.0.0.1:24220/api/plugins.json`. Primary IPv6 pod probes также
работают. Endpoint сам по себе не подтверждает доставку в Loki — это проверяет CLI.

Positions `/var/lib/fluentd/nginx.pos` и file buffer
`/var/lib/fluentd/buffer/loki` сохраняются на hostPath после замены pod.
Буфер ограничен 256MiB, chunk 1MiB, одна flush thread, retry с задержкой
до 30s и без срока истечения. При заполнении `overflow_action block`
приостанавливает обработку; после восстановления downstream она
возобновляется. CRI rotation во время продолжительной остановки может
удалить еще не прочитанные записи; отсутствие потерь при любом outage
не заявляется. Доставка допускает дубли. Plugin повторяет HTTP 429/5xx и
сетевые ошибки; HTTP 4xx кроме 429 не повторяет, поэтому ошибки схемы,
retention или слишком старых данных требуют проверки логов collector.

```bash
helm lint deploy/logging
helm template logging deploy/logging --namespace logging
python3 -m unittest discover -s deploy/logging/tests -p 'test_*.py' -v
# Дополнительный локальный тест, требует Docker и PyYAML:
.venv/bin/python deploy/logging/tests/docker_smoke.py --helm .cache/tools/helm
```

Docker smoke требует IPv6 на Docker host (проверен 29.8.0). Создает
уникальные временные containers в dual-stack сети, публикует порты Loki
только на `::1` и `127.0.0.1`, пишет synthetic CRI fixtures в `/tmp`.
Проверяет настоящий parser, plugin, Loki config/API по обеим семьям,
DNS A/AAAA из Fluentd, monitor обеих loopback семей, persistent buffer
и restart при коротком outage. Это проверка с fixtures, не приемка Kubernetes.

## Проверка

`make verify ENV=ubuntu` отправляет запросы и находит их access/error-записи
в Loki. Ручные команды приведены в
[README](../../README.md#проверка-логирования).

Ниже показан вызов скрипта внутри общего сценария: `RUN_ID` и `STARTED_AT`
содержат идентификатор и время начала проверки, Loki доступен через
port-forward на указанном порту. В режиме `ipv4` используйте
`http://127.0.0.1:13100`. Скрипт только читает Loki, не создает HTTP-запрос:

```bash
python3 scripts/check-logs.py --loki-url 'http://[::1]:13100' \
  --run-id "$RUN_ID" --request-id "$RUN_ID" --error-marker "$RUN_ID-err" \
  --start-time "$STARTED_AT" --timeout 120 --output /tmp/logs-report.json
```

Самостоятельная проверка принимает `--base-url 'http://[2001:db8::10]:30080'` вместо
пары `--request-id`/`--error-marker`; создает ровно access GET `/` и error
GET `/verify/missing/<marker>`. Переданные IDs никогда не заменяются.
CLI принимает URL с native IPv4 или bracketed IPv6 literal; IPv4-mapped
и scoped IPv6 адреса отклоняет. CLI проверяет exact ID/method/URI/numeric status, оригинальный JSON message,
реальный missing-file stderr, labels, source path и CRI timestamp. Отчет
создается также при timeout/ошибке и не содержит исходного stderr с адресами.

`make verify-recovery ENV=ubuntu` проверяет сохранение истории в PVC,
доставку записей после недоступности Loki и замены Fluentd, а также
сохранность позиций чтения. Сценарий временно меняет тестовый стенд;
[результаты](../../docs/verification.md) учитываются отдельно от локальных тестов.
