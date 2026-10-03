# Состояние проекта

Актуальная чистая приемка от 2026-10-03 — `pass`, код
`c268c5fb47759db76a661a014ffd56a8974c8f51`.
На новой Ubuntu 24.04.5 единый `bash scripts/deploy.sh --env ubuntu`
установил недостающие инструменты, Kubernetes и весь стек за 312,30 с.
Пройдены 180 тестов lint, health, девять полных MVP verify, шесть TLS/Gateway
прогонов и отдельная внешняя проверка, два повторных bootstrap/deploy,
шесть canary-сценариев, interruption, app/PVC/buffer recovery и reboot.
Stable readiness gate проверен до кандидата, до 90/10 и перед `accepted`;
негативные состояния stable покрыты regression fixtures.
[Независимый протокол](acceptance/clean-ubuntu-2026-10-03.md),
[JSON evidence](acceptance/clean-ubuntu-2026-10-03.json).
Область — MVP и назначенные FINAL-01/05; подготовка комплекта сдачи не выполнялась.
Далее сохранена история прежних приемок.

Полноценный dual-stack Kubernetes с основным IPv6 принят на кодовой ревизии
`cc3afc6cab06a4cb441c3f0c705e79cc04910a0f`. IPv4 доступен приложению и компонентам.
[Независимый протокол](acceptance/dual-stack-acceptance.md),
[JSON evidence](acceptance/dual-stack-evidence.json), [сетевая инструкция](networking.md).

Последняя отдельная приемка MVP относится к `8ed0e70`: после исправления
двух пробелов строгости проверок повторный аудит рабочего стенда прошел.
[Отчет](reports/MVP-AUDIT.md), [новые доказательства](acceptance/mvp-audit.json).
Чистая установка относится к `cc3afc6`; для `8ed0e70` новый pristine/reboot
прогон не выполнялся.

| ID | Статус | Владелец | Результат |
| --- | --- | --- | --- |
| MVP-00 | done | интегратор | Контракт v3, закрепленные версии, 20 image digests, checksum downloads |
| MVP-01 | done | инфраструктура | Чистая Ubuntu → kubeadm/Calico dual-stack, IPv6 primary; повтор bootstrap/health pass; [отчет](reports/DUAL-INFRA.md) |
| MVP-02 | done | Gateway | Внешний HTTP, Request-ID и route/error cases по IPv6/IPv4; [отчет](reports/DUAL-COMPONENTS.md) |
| MVP-03 | done | метрики | 5 уникальных IPv6 scrape endpoints, counter не менее +48 после 24+24 запросов; [отчет](reports/DUAL-COMPONENTS.md) |
| MVP-04 | done | логирование | Exact access/error обеих семей, история и все 5 buffer markers после рестарта; [отчет](reports/DUAL-COMPONENTS.md) |
| MVP-05 | done | интегратор | Общий verify 11/11, repeat, recovery, включение VM и poisoned-proxy verify pass; [отчет](reports/DUAL-INTEGRATION.md) |
| MVP-06 | done | независимая приемка | Новая чистая Ubuntu, 11 стадий, 4 verify; внешние проверки и финальный возврат основного стенда; [протокол](acceptance/dual-stack-acceptance.md) |

Основной dual-stack стенд работает. Приемочная и прежние VM штатно остановлены,
их диски и данные сохранены. TSDB из прежних кластеров не переносилась.
Изменения VM выполнял только интегратор; независимый агент сверял код,
negative fixtures, raw snapshots, вложенные проверки и evidence archives.
Назначенные пользователем FINAL-01 и FINAL-05 приняты в области TLS/Gateway,
выпуска canary и восстановления на коде `9ce5f99`.
[Протокол](reports/FINAL-INTEGRATION.md), [JSON evidence](acceptance/final-01-05.json).
Остальные карточки FINAL не назначены. Полный профиль FINAL не заявляется;
alerts и их recovery остаются в FINAL-02.

| ID | Статус | Область |
| --- | --- | --- |
| FINAL-01 | done | TLS, host/path routes, два backend, 90/10 по обеим семьям; live pass |
| FINAL-05 | done | Назначенная область: HTTP/metrics gate, автооткат, interruption, pod/config/PVC/buffer recovery, reboot; live pass |

Первоначальная IPv4-приемка `337d0c6` сохранена как [история](acceptance/mvp-acceptance.md).
Строгая IPv6-only попытка заменена новым требованием и не имеет принятой ревизии;
отозванный промежуточный вердикт и исходные отказы не подменяют новую приемку.
В dual-stack прогоне `09d3410` найден и исправлен race в наблюдении Fluentd
positions; окончательная чистая приемка выполнена заново на `cc3afc6`.
Дополнительный аудит `8ed0e70` усилил сопоставление Gateway status с `node_ip`
и проверку repository/tag/digest в image lock; новые regression-тесты и
повторные live-проверки прошли. До назначения FINAL-01/05 после этой ревизии
менялась только документация; принятые расширения отражены выше.
