# FINAL-01 — Gateway, TLS и два backend

Дата: 2026-10-02. Статус компонента: `ready_for_integration`.
База: `c6d9e25`. Код для интеграции: `d772dc4`.
Live-приемка выделенной Ubuntu VM выполняется интегратором; этот отчет
различает render/fixture проверки и реальный стенд.

Измененные пути: `deploy/app/`, `deploy/gateway/`,
`scripts/check-gateway.final.py`, `scripts/check-gateway.container.py`,
`docs/reports/FINAL-01.md`. Общие файлы, VM и Kubernetes компонентным
агентом не изменялись. Полный интерфейс — в [Gateway README](../../deploy/gateway/README.md).

## Реализация

- Сохранены stable ресурсы `hello`, исходные selectors и 12-byte body.
  Stable имеет 2 replicas с `maxUnavailable: 0`. Canary — отдельный
  release того же chart с `fullnameOverride: hello-canary`.
- `releaseTrack` / `releaseRevision` присутствуют в HTTP headers и
  JSON access; Loki labels не менялись. Schema защищает конфигурацию
  Nginx от некорректных значений revision.
- Явные canary demo-режимы `http500` и `wrong-body` сохраняют здоровую
  readiness probe. Schema запрещает эти режимы для stable.
- Четыре routes: `hello`, `hello-host`, `hello-stable`, `hello-canary`.
  Первые два разделяют weights default 100/0, сумма строго 100.
  Последние два маршрутизируют `/backend/<track>` через стандартный
  URLRewrite `/` к конкретному backend при любых весах.
- HTTPS listener `websecure:8443`, NodePort 30443, TLS Terminate через
  `hello-tls`. Secret template принимает certificate/privateKey/caCertificate
  вместе. Генерация и безопасное хранение ключей — общий deploy интегратора.
- Read-only checker `--env ubuntu [--split]`: актуальные conditions и
  generation, точные refs/weights/filters, обе IP-семьи, host/path,
  CA/name, отрицательные TLS-пробы. По 1000 запросов на семью без retries;
  90/10 принимает 5–15% canary, 100/0 требует ноль canary. Любой неверный
  ответ или потерянный запрос дает fail. Routes проверяются до/после.

## Проверки

Команды выполнялись из `/home/Bebroid/mts_hack/.worktrees/final-01`.
Для тестов были заданы:

```sh
export MTS_TEST_HELM=/home/Bebroid/mts_hack/.cache/tools/helm
export MTS_TEST_TRAEFIK_CHART=/home/Bebroid/mts_hack/.cache/downloads/traefik.tgz
```

| Статус | Команда / сценарий | Результат |
| --- | --- | --- |
| pass | `/home/Bebroid/mts_hack/.cache/tools/helm lint deploy/app deploy/gateway` | Оба собственных chart |
| pass | `/home/Bebroid/mts_hack/.venv/bin/python deploy/app/tests/test_render.py` | 3 теста; identities/selectors, checksum, rollout, faults, schema negatives |
| pass | `/home/Bebroid/mts_hack/.venv/bin/python deploy/gateway/tests/test_render.py` | 7 тестов; pinned Traefik render, HTTPS ports, routes/weights, TLS secret/schema negatives |
| pass | `/home/Bebroid/mts_hack/.venv/bin/python deploy/gateway/tests/test_final.py` | 12 fixture tests; stale/missing/duplicate conditions, wrong refs/weights/routes, invalid HTTP/revision, statistical gates, explicit kubeconfig, verified curl TLS args, details at deadline, pinned attachedRoutes semantics |
| pass | `/home/Bebroid/mts_hack/.venv/bin/python -m py_compile scripts/check-gateway.final.py scripts/check-gateway.container.py deploy/app/tests/test_render.py deploy/gateway/tests/test_render.py deploy/gateway/tests/test_final.py` | Python syntax |
| pass | `/home/Bebroid/mts_hack/.venv/bin/yamllint -c .yamllint deploy/app/values.yaml deploy/gateway/values.yaml deploy/gateway/traefik-values.yaml` | YAML lint |
| pass | `git diff --check` | Whitespace |
| fail | Локальный container smoke с закрепленным Nginx digest | Docker container/network созданы; HTTP `/healthz` через опубликованный IPv6 loopback дал timeout; ресурсы удалены в finally |
| not run | Реальные container-пробы canary headers/http500/wrong-body | Добавлены в smoke после первого запуска; host loopback timeout не позволяет подтвердить локальный сетевой путь |
| not run | Ubuntu Gateway/TLS/1000 requests и возврат 100/0 | Только интегратор выполняет живые изменения и приемку |

Команда неуспешного container smoke (публичный digest, без секретов):

```sh
MTS_TEST_HELM=/home/Bebroid/mts_hack/.cache/tools/helm \
  /home/Bebroid/mts_hack/.venv/bin/python scripts/check-gateway.container.py \
  --image docker.io/nginxinc/nginx-unprivileged:1.28.0-alpine@sha256:c97ff0bf7cbae369953c6da1232ec14ad9f971d66360c5698db0856a4cd657a0
```

## Интеграционная приемка

Первый live deploy интегратора прошел MVP gate. Первый FINAL checker
остановился на `attachedRoutes`: ожидались 4, получены 8 у обеих listeners.
Разбор закрепленных функций Traefik 3.6.2 подтвердил повторное включение
listener для каждого parentRef к Gateway. Обоснование и ссылки на исходники
приведены в [Gateway README](../../deploy/gateway/README.md).
Checker исправлен на строгое значение `4 × 2 = 8` с отдельными проверками
полного набора из четырех routes и ровно двух корректных parent statuses.
Негативные fixtures отклоняют 4/7/9, другую версию controller, пропущенные
или лишние routes/parents даже при счетчике 8. Повторная live-приемка этого
исправления остается за интегратором.

После общего deploy с обоими backend и TLS CA:

```sh
.venv/bin/python scripts/check-gateway.final.py --env ubuntu \
  --output artifacts/final-01-100-0.json
# Интегратор меняет веса через общий gateway Helm release на 90/10.
.venv/bin/python scripts/check-gateway.final.py --env ubuntu --split \
  --output artifacts/final-01-90-10.json
# Интегратор возвращает 100/0 и повторяет первый checker + MVP verify.
```

До этих прогонов компонент не считается `done`. Нужны оба HTTPS family
paths, отрицательные CA/name проверки, кандидат при нулевом весе,
нормальные и ошибочные canary answers, статистика 90/10 и новое отсутствие
canary после возврата 100/0.

Источники: [Traefik 3.6 Gateway API](https://doc.traefik.io/traefik/v3.6/reference/routing-configuration/kubernetes/gateway-api/),
[Gateway API URLRewrite](https://gateway-api.sigs.k8s.io/guides/user-guides/http-redirect-rewrite/),
[Gateway API TLS](https://gateway-api.sigs.k8s.io/guides/user-guides/tls/).
