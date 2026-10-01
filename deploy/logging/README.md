# Fluentd → Loki для MVP

Chart `logging` ставится в namespace `logging`. Интегратор передает
`images.loki`, `images.fluentd`, `dataRoot`; общий post-renderer заменяет image
tags принятым digest lock. Loki требует заранее созданный PV `mts-loki`,
StorageClass `local-retain` и директорию данных с UID/GID 10001:10001.
Fluentd требует каталог `<dataRoot>/fluentd` root:root 0750, который создает
bootstrap. Произвольные host paths сам chart не создает.

Loki использует один Deployment, PVC `loki` 12Gi, TSDB/v13, filesystem,
WAL и compactor на одном volume. Retention 24h применяется compactor
асинхронно; это не жесткая дисковая квота. Удаление chunk после retention
отложено еще на 2h. Нужен контроль свободного места; резервирования и HA нет.
ClusterIP 3100 предназначен для внутренней сети, authentication отключена.

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
запрещен. Monitor endpoint 24220 показывает состояние процесса collector;
он сам по себе не подтверждает доставку в Loki — это проверяет CLI.

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

```sh
helm lint deploy/logging
helm template logging deploy/logging --namespace logging
python3 -m unittest discover -s deploy/logging/tests -p 'test_*.py' -v
# Дополнительный локальный тест, требует Docker и PyYAML:
.venv/bin/python deploy/logging/tests/docker_smoke.py --helm .cache/tools/helm
```

Docker smoke создает уникальные временные containers/network, loopback
порт Loki и synthetic CRI fixtures в `/tmp`. Он проверяет настоящий parser,
plugin, Loki config/API, persistent buffer и restart при коротком outage.
Это отдельная проверка реализации с fixtures, не приемка Kubernetes.

Проверка общего прогона только читает Loki, не создает новый HTTP запрос:

```sh
python3 scripts/check-logs.py --loki-url http://127.0.0.1:13100 \
  --run-id "$RUN_ID" --request-id "$RUN_ID" --error-marker "$RUN_ID-err" \
  --start-time "$STARTED_AT" --timeout 120 --output /tmp/logs-report.json
```

Самостоятельная проверка принимает `--base-url http://NODE:30080` вместо
пары `--request-id`/`--error-marker`; создает ровно access GET `/` и error
GET `/verify/missing/<marker>`. Переданные IDs никогда не заменяются.
CLI проверяет exact ID/method/URI/numeric status, оригинальный JSON message,
реальный missing-file stderr, labels, source path и CRI timestamp. Report
создается также при timeout/ошибке и не содержит сырого stderr с адресами.

Live приемку на выделенной VM выполняет интегратор: deploy/verify, новая
проба при остановленном Loki, restart Fluentd, восстановление Loki и поиск
той же пробы, повторный deploy с сохранением PVC и прежних records.
