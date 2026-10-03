# Повторная чистая установка — 3 октября 2026 года

**Результат: pass.** Интегратор повторил установку ревизии
`27d775b76a5ae0021911293de137a4c3d5efc53c` на новой Ubuntu 24.04.5 LTS amd64
(4 vCPU, 8192 MiB RAM, диск 60 GiB). Единственный установочный вход:

```bash
bash scripts/deploy.sh --env ubuntu
```

Команда завершилась с exit 0 за **345,15 с (5 мин 45 с)**. Ручные установки
пакетов, отдельный bootstrap, правки Kubernetes-ресурсов и исправления кода
не потребовались. Новая VM оставлена работающей, веса stable/canary — 100/0.
Существующие VM, текущий Kubernetes и router не изменялись.
[Машинный протокол и SHA-256 evidence](CLEAN-INSTALL-RECHECK-2026-10-03.json).
Это повтор интегратора, а не новое независимое ревью.

## Исходное состояние и граница автоматизации

До переноса исходников зафиксировано отсутствие `/etc/kubernetes`,
`/etc/mts-stack`, `/var/lib/kubelet`, `/var/lib/containerd` и checkout.
Отсутствовали kubeadm, kubelet, kubectl, containerd, Helm, Ansible, make
и Python venv. Все 145 файлов переданного checkout побайтово совпали с Git;
`.venv`, `.cache`, `.state` и artifacts прежних установок не переносились.
В конце основного прогона Git working tree на VM оставался чистым.

Предварительная подготовка ограничивалась созданием VM из cloud image
с проверкой SHA-256, доступом SSH/sudo, передачей исходников и явным локальным
environment. Cloud-init установил только гостевой агент гипервизора.
SSH host key проверен через доверенный гипервизор; приватные ключи,
kubeconfig и реальные адреса остаются только в игнорируемых локальных файлах.
Создание VM, SSH-доступ и параметры environment являются входными условиями
установщика; сам deploy от гипервизора не зависит.

Установщик самостоятельно подготовил системные prerequisites, `.venv`,
Ansible, Helm и kubectl; настроил DNS/NTP, swap/sysctl, containerd,
kubeadm/Kubernetes, Calico и постоянные каталоги. Bootstrap: 110 успешных
задач, failed/unreachable/rescued/ignored — 0. Затем установлены восемь
Helm releases: storage, monitoring, traefik, monitoring-extras, logging,
app, app-canary и gateway, включая локальную CA и TLS-сертификат.

При первоначальном старте наблюдались временные FailedScheduling и
FailedCreatePodSandBox до готовности Calico. Они разрешились автоматически;
контроллеры и встроенные DNS/HTTP-пробы обеих семей стали готовы в пределах
таймаутов. Ручных изменений для продолжения установки не было.

## Выполненные проверки

Команды таблицы запускались на новой VM с ее собственным `ENV=ubuntu`.
Все перечисленные live-результаты получены в этом прогоне.

| Команда / проверка | Результат | Время |
| --- | --- | ---: |
| `bash scripts/deploy.sh --env ubuntu` | pass: весь стек с чистой ОС | 345,15 с |
| `make lint` | pass: 180 unit/fixture tests в 9 suites, syntax, 7 Helm lint/render, image lock | 16,05 с |
| `make health ENV=ubuntu` | pass: узел, DNS и pod/Service HTTP по IPv6/IPv4 | 45,87 с |
| `make verify-release ENV=ubuntu` | pass: MVP 11/11 и Gateway/TLS 154/154 | 26,68 с |
| `make verify-idempotency ENV=ubuntu` | pass: два полных bootstrap/deploy/verify | 326,60 с |
| `make evidence ENV=ubuntu` | pass: архив отчетов | 0,11 с |
| Внешний `scripts/check-gateway.final.py --env <локальное-имя> --requests 100 --run-id clean-recheck-20261003 --output <локальный-отчет>` | pass: 154/154, HTTP/TLS обеих семей, оба backend, отрицательные CA/hostname | — |
| Контролируемый reboot через штатную `release-demo.reboot()` с внешнего controller | pass: boot ID изменился, API восстановился | 35,73 с до API |
| `make health ENV=ubuntu` после reboot | pass: containerd/kubelet активны, сеть и DNS работают | 73,84 с |
| `make verify-release ENV=ubuntu` после reboot | pass: MVP 11/11 и Gateway/TLS 154/154 | 38,11 с |
| `make evidence ENV=ubuntu` после reboot | pass: итоговый архив | 0,36 с |

Основной драйвер от deploy до первого evidence занял 760,84 с; создание VM,
передача checkout, внешние проверки и reboot в это время не входят.
После reboot службы и приложение восстановились без ручного запуска.

В четырех MVP-прогонах проверены HTTP, Request-ID, точные access/error-записи
в Loki, runtime security и реальные Prometheus targets. Приросты scoped
HTTP counter — 48, 49, 49 и 48 при требуемом минимуме 48 в каждом прогоне.
Два повторных deploy сохранили 28 identities ресурсов, привязки двух PVC
и содержимое пяти прикладных Secrets, включая TLS. Все последующие verify
прошли. Внешняя проверка дополнительно выполнила по 100 запросов каждой семьи.

Локальные проверки CLI: `--help` — exit 0; неполный `--env`, недопустимое
имя `../ubuntu`, отсутствующий environment, неизвестный флаг и лишний
позиционный аргумент — exit 2 до запуска установки. Эти 6 CLI-проверок
учтены отдельно от 180 unit/fixture tests и live-проверок.

Сверены 9 итоговых report/gateway JSON, их ревизии и успешные checks.
Содержимое двух evidence-архивов (6 и 9 отчетов) совпадает с исходными JSON.
Сырые отчеты и журналы сохранены локально в игнорируемом `.state/`;
публичный JSON содержит только сводку и контрольные суммы.

## Границы повторной проверки

`not run`: полный `make demo`, canary fault/rollback, interruption и
PVC/buffer recovery — не входят в этот повтор установки; их прежняя
[чистая приемка](../acceptance/clean-ubuntu-2026-10-03.md) сохранена отдельно.
Неназначенные FINAL-карточки не выполнялись. Повтор не расширяет область
принятых функций и не заменяет профиль `PROFILE=final`.
