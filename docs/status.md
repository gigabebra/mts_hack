# Состояние MVP

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
Назначены пользователем FINAL-01 и FINAL-05. Разработка идет от принятого MVP;
остальные карточки FINAL не назначены. Полный профиль FINAL не заявляется.

| ID | Статус | Область |
| --- | --- | --- |
| FINAL-01 | in_progress | TLS, host/path routes, два backend, traffic split |
| FINAL-05 | in_progress | HTTP/metrics gate кандидата, автоматический откат, сценарии восстановления |

Первоначальная IPv4-приемка `337d0c6` сохранена как [история](acceptance/mvp-acceptance.md).
Строгая IPv6-only попытка заменена новым требованием и не имеет принятой ревизии;
отозванный промежуточный вердикт и исходные отказы не подменяют новую приемку.
В dual-stack прогоне `09d3410` найден и исправлен race в наблюдении Fluentd
positions; окончательная чистая приемка выполнена заново на `cc3afc6`.
Дополнительный аудит `8ed0e70` усилил сопоставление Gateway status с `node_ip`
и проверку repository/tag/digest в image lock; новые regression-тесты и
повторные live-проверки прошли. До назначения FINAL-01/05 после этой ревизии
менялась только документация; текущая разработка расширения отражена выше.
