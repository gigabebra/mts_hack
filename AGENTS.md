# Работа над MVP

Следовать `docs/implementation-plan.md`, `docs/agent-sessions.md` и
`docs/contracts.md`. Этап FINAL пока не назначен.

Интегратор один изменяет общие файлы (`config/versions.yaml`,
`config/images.lock.yaml`, `docs/contracts.md`, `Makefile`, `scripts/deploy.*`,
`README.md`, `docs/status.md`), объединяет ветки и работает с общей Ubuntu VM.
Компонентные агенты работают в выданных отдельных worktree и ветках.
До трех исполнителей одновременно, помимо интегратора.

Владельцы:
- MVP-01: `ansible/`, `deploy/storage/`, `config/environments/ubuntu.example.yaml`.
- MVP-02: `deploy/app/`, `deploy/gateway/`, `scripts/check-gateway.*`.
- MVP-03: `deploy/monitoring/`, `scripts/check-metrics.*`.
- MVP-04: `deploy/logging/`, `scripts/check-logs.*`.
- MVP-05: интегратор; общие deploy/verify/evidence, документация.
- MVP-06: независимый агент; `docs/acceptance/`, `docs/passport/`, отчет.

Каждый исполнитель может писать `docs/reports/<свой ID>.md`. Изменения общих
контрактов предлагает интегратору до зависимой реализации. Не править соседние
репозитории, действующие VM, домашний router и текущий Kubernetes.
Использовать только явно переданный kubeconfig; не использовать current-context.
Все живые изменения стенда выполняет интегратор последовательно.

Проверять собственные lint/render и подходящие негативные случаи. Реальные
проверки отличать от fixtures: `pass`, `fail`, `not run` с причиной.
`ready_for_integration` не означает `done`: последнее требует live-приемки.
Не скрывать ошибки, не ослаблять проверки ради зеленого результата.
Не коммитить credentials, kubeconfig, реальные адреса и сырые чувствительные логи.
Передать интегратору SHA коммита, измененные пути и точные команды проверок.
