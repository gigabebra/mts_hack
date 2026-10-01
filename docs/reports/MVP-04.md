# MVP-04 — Fluentd и Loki

> Исторический отчет первоначального MVP на IPv4; итоговая приемка этого
> этапа относится к `337d0c6`. Приведенные ниже статусы, команды и числа
> описывают ту ревизию, а не текущий dual-stack. Актуальный результат:
> [интеграция dual-stack](DUAL-INTEGRATION.md) и
> [независимая приемка](../acceptance/dual-stack-acceptance.md).

Статус: `ready_for_integration`; `done` требует live-приемки интегратором.
База: `3874474e11f4812f8366b8f2a0464df7584f34ed`, ветка `mvp-04`.
Итоговая ревизия передается интегратору вместе с этим отчетом.

Измененные пути: `deploy/logging/`, `scripts/check-logs.py`, этот отчет.
Общие файлы, другие worktree и VM не изменялись.

Собственный chart 0.1.0 содержит Loki Deployment/Service/PVC `loki` и
DaemonSet `fluentd`. Loki 3.6.2: UID10001, filesystem TSDB/v13, retention24h,
WAL/compactor на PVC12Gi `mts-loki`. Fluentd 1.19.0 и Loki plugin берутся
из принятого image digest; запуск явно включает `-p /fluentd/plugins`.
CRI и JSON parsers — встроенные. Позиции и ограниченный file buffer
сохраняются на `<dataRoot>/fluentd`, symlinks containers→pods доступны
read-only. Collector не требует доступа к Kubernetes API/RBAC.

CLI сохраняет JSON по общему контракту и ненулевой exit при ошибке.
Переданные request-id/error-marker только ищет, новые probes не создает.
Самостоятельный `--base-url` создает ровно два HTTP запроса. Access проверяется
по exact ID/method/URI/numeric status и исходному JSON; error — по
stderr Nginx `open() ... failed (2: No such file or directory)` с точным
request URI. Дополнительно проверяются источник, наносекундное CRI время
и ровно четыре разрешенных labels. Report не содержит сырые error logs.

| Проверка | Результат | Основание |
| --- | --- | --- |
| Helm lint/render | pass | Chart и resource names валидны |
| YAML lint | pass | Общая конфигурация `.yamllint` |
| dataRoot override | pass | `/srv/mts-data/fluentd` в рендере |
| Относительный dataRoot | pass | Негативный тест: Helm schema отвергает `relative` |
| Python compile + 7 protocol/negative tests | pass | Неправильные ID/path/status/source/time/labels, API errors, no-probes invariant |
| Fluentd config dry-run | pass | Настоящий Fluentd1.19.0 + plugin в принятом image |
| Loki config verify | pass | Настоящий Loki3.6.2 `-verify-config=true` |
| CRI stdout/stderr → Fluentd → Loki API | pass | Изолированные Docker процессы, synthetic CRI fixtures |
| Collector restart при коротком outage | pass | Буфер пережил restart Fluentd; новая проба найдена после возврата Loki, прежняя запись сохранилась |
| Kubernetes install/rollout/PVC | not run | Только интегратор работает с общей VM |
| Live HTTP→Nginx→Loki общий Request-ID | not run | Передается MVP-05 |
| Повторный deploy и VM recovery | not run | Передается MVP-05 |
| Фактическое заполнение 256MiB buffer | not run | Настроено block/back-pressure, режим описан; долгий saturation drill не выполнялся |

Команды из корня worktree:

```sh
.cache/tools/helm lint deploy/logging
.cache/tools/helm template logging deploy/logging --namespace logging
.cache/tools/helm template logging deploy/logging --namespace logging --set dataRoot=/srv/mts-data
# Expected nonzero (негативный тест):
.cache/tools/helm lint deploy/logging --set dataRoot=relative
.venv/bin/yamllint -c .yamllint deploy/logging/Chart.yaml deploy/logging/values.yaml deploy/logging/files/loki.yaml
python3 -m py_compile scripts/check-logs.py
python3 -m unittest discover -s deploy/logging/tests -p 'test_*.py' -v
.venv/bin/python deploy/logging/tests/docker_smoke.py --helm .cache/tools/helm
git diff --check
```

Первый полный Docker smoke завершился fail из-за test harness: случайный
published port Loki менялся при restart. Config/первый ingestion были pass.
Harness исправлен: после restart получает актуальный loopback port.
Повтор полного smoke завершился pass, временные containers/network удалены.
Еще два ранних запуска выявили ограничения `--internal` Docker network
для published port и startup connection reset; эти ошибки harness исправлены.

Изменений общего контракта не требуется. Интеграционные values:
`images.loki`, `images.fluentd`, `dataRoot`. Labels pod:
`app.kubernetes.io/name=loki` и `app.kubernetes.io/name=fluentd`.
Port Loki3100, Fluentd health24220. Images renderer передает через общий lock.

Ограничения: одиночный Loki/filesystem/Retain PV не обеспечивают HA или
резервное копирование; retention не является лимитом диска. File buffer
256MiB использует back-pressure, но продолжительная ротация CRI может
удалить еще не прочитанные записи; дубли допустимы. Loki plugin повторяет
429/5xx и сетевые отказы, но не другие 4xx. CRI partial (`P`) сохраняется
как отдельная запись, сборка многофрагментных сообщений не заявляется.
Проверяемые короткие Nginx записи полные (`F`). Подробнее —
`deploy/logging/README.md`.

Следующий шаг: интегратор применяет ветку, выполняет общий deploy/verify,
останавливает Loki на короткий срок, делает пробы, заменяет Fluentd pod,
восстанавливает Loki и находит исходные markers, затем повторяет deploy.

## Итог интегратора

Статус: done, 2026-10-01. На kubeadm VM найдены exact access-ID и настоящий stderr missing-file error в Loki. История пережила замену Loki pod. При коротком outage пять маркеров сохранились в буфере через замену Fluentd pod и найдены после восстановления. Tail offsets до/после равны; новый input emit_records=0 до следующего трафика: старые логи не перечитаны. Прогон recovery-162280d8e552 завершен зеленым verify. Полное заполнение 256MiB не проверялось и не заявляется.
Сводка без адресов и credentials: [mvp-integration.json](../acceptance/mvp-integration.json).
