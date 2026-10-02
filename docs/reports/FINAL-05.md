# FINAL-05: выпуск canary и автоматический возврат

Статус компонентной реализации: `ready_for_integration`, не `done`.
База: `c6d9e25`; ветка `final-05`, отдельный worktree `.worktrees/final-05`.
Live-приемку и изменения выделенной VM выполняет интегратор.

Реализованы `scripts/canary.py`, `scripts/canary_metrics.py`,
`tests/canary/verify.py`, `tests/canary/test_canary.py` и этот отчет.
Общие файлы и соседние компоненты не изменялись.

## Поведение

Обычный выпуск под общей блокировкой deploy читает фактические значения Helm и
веса маршрутов, сначала подтверждает 100/0, затем устанавливает отдельный
`app-canary` из закрепленного образа. Smoke идет через `/backend/canary`:
проверяются точные 12 байт `Hello World!`, статус 200, `X-Release`, запрошенный
`X-Revision` и Request-ID. Проверка Deployment подтверждает rollout, число
реплик, закрепленный образ и фактические значения release.

После свежего scrape, покрывающего direct smoke, включаются веса 90/10.
Основной маршрут получает ограниченную нагрузку 40 запросов/с в течение 60 с
(окно допускает 60–120 с). Принимается только кандидат с минимум 100 ответами
точной ревизии, минимум 100 запросами по счетчику backend, правильными HTTP
ответами, свежими samples и долей 5xx не более 1%. Кандидат остается на 10%.

Метрики читаются как range-vector с исходными timestamps, без `rate()` и
подмены отсутствующих данных нулем. Проверяются baseline sample, reset,
неизменность exporter process identity, свежий `up`, единственность scrape,
полная идентичность backend/target. Новая серия 500 учитывается только при
подтвержденной непрерывности процесса и исходных счетчиков. Данные одного
leaf backend не суммируются с WRR-счетчиками маршрутов.

Точное имя `demo-hello-canary-http-80@kubernetesgateway` выведено из
[исходников Traefik v3.6.2](https://raw.githubusercontent.com/traefik/traefik/v3.6.2/pkg/provider/kubernetes/gateway/httproute.go):
формирование имени использует namespace, Service name, `http` и port.
Scrape label `service=traefik-metrics` и exporter label `exported_service`
должны быть подтверждены на стенде интегратором.

При отказе возвращаются веса 100/0 через Helm, проверяются 25 новых запросов
основного маршрута только к stable, устанавливается заведомо здоровая базовая
конфигурация кандидата и проверяется ее точная ревизия. Это восстановление
зафиксированных defaults, а не произвольной прежней потенциально сломанной
версии. Недостаток/недоступность метрик дает `inconclusive`; неисправный HTTP —
`rejected`; сбой самого возврата — `rollback_failed`. Все три результата
возвращают exit 2. SIGTERM/SIGINT и общий timeout также запускают восстановление.
SIGKILL обработать нельзя: следующий выпуск заново читает кластер и начинает
с подтвержденного 100/0. Доступна отдельная команда `--action restore`.
Несогласованные веса двух HTTPRoute и отсутствующая история кандидата после
прерванной операции сохраняются как диагностика, но не блокируют восстановление.
После восстановления все строгие проверки весов и состояния обязательны.

Отчеты `artifacts/canary/<run-id>/report.json` и aggregate acceptance сохраняют
фактическую Git-ревизию исполняемого checkout и `working_tree_dirty`.
Неизвестная ревизия запрещает изменение стенда. Настраиваемый hostname
принимается только как корректное ограниченное DNS-имя в `.test`.

Отчеты `artifacts/canary/<run-id>/report.json` содержат whitelisted значения
и ревизии Helm, этапы/времена, наблюдаемые веса, HTTP evidence, raw samples,
пороговое решение и результат восстановления. Произвольные Helm values,
TLS keys, kubeconfig, адреса scrape и тела HTTP не сохраняются в JSON;
тело представлено длиной и SHA-256.

## Проверки

| Проверка | Результат | Команда / пояснение |
| --- | --- | --- |
| Engine/metrics/acceptance fixtures | pass | `python3 -m unittest discover -s tests/canary -p 'test_*.py' -v` — 36 тестов |
| Синтаксис Python | pass | `python3 -m py_compile scripts/canary.py scripts/canary_metrics.py tests/canary/verify.py tests/canary/test_canary.py` |
| Интерфейс CLI | pass | `python3 scripts/canary.py --help`; `python3 tests/canary/verify.py --help` |
| Whitespace | pass | `git diff --check` |
| Live выпуск, сбои и возврат | not run | Компонентный агент не работает с общей VM; запускает интегратор после интеграции |
| Общее восстановление pod/Loki/PVC и reboot | not run этим агентом | Это часть интеграции и общего recovery; данный отчет их успех не заявляет |
| FINAL-02/03, alerts/security/full final verify | not run | Эти карточки не назначены; gate использует принятый MVP Prometheus |

Fixtures покрывают здоровый выпуск; неверное тело/ревизию до трафика;
500/неверное тело после 90/10; недоступность метрик; недостаточный HTTP/metric
sample; stale/missing/duplicate metrics; новый code=500; reset посреди окна;
перезапуск exporter; прерывание/timeout; неуспех самого rollback; реальный код
возврата CLI после успешного rollback. Отдельная регрессия запрещает назвать
отказ установки fault-конфигурации успешно наблюдавшимся HTTP-сбоем.

Live harness последовательно запускает шесть сценариев: healthy, http500,
wrong-body после 90/10, wrong-body до трафика, metrics-unavailable,
insufficient-sample. Fault hooks находятся только в тесте, не в production CLI.
500 и wrong-body требуют фактического ошибочного ответа именно запрошенного
canary; простого ненулевого результата команды недостаточно. Недоступность
метрик создается остановкой реального local port-forward проверки, не
отключением общего Prometheus. Недостаточная выборка создается ограничением
общей нагрузки до 20 запросов при неизмененном минимуме 100 canary samples.

После здорового 90/10 и финального cleanup 100/0 harness запускает
`scripts/verify-release.py --env <environment>`; блокировка удерживается
на протяжении всего набора, включая проверки и cleanup.

## Передача интегратору

Обязательные зависимости: согласованный `scripts/release_control.py`,
`config/canary.yaml`, app/Gateway chart из FINAL-01 и
`scripts/verify-release.py`. Параметры и методы согласованы сообщениями с
интегратором; согласование интерфейса не заменяет live-приемку.

После общей интеграции:

```sh
python3 scripts/canary.py --env ubuntu --revision candidate-v1
python3 tests/canary/verify.py --env ubuntu --output artifacts/canary/acceptance.json
python3 scripts/canary.py --env ubuntu --action restore
```

Отдельные live-сценарии выбираются `--test-scenario` у harness. Для ошибки до
трафика можно явно передать `--fault-mode wrong-body` обычному выпуску.
Повтор обычного deploy обязан задавать здоровый кандидат и веса 100/0;
это реализует интегратор, не данная компонентная ветка.
