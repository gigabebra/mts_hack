# MVP-05 — общая интеграция

> Исторический отчет первоначального MVP на IPv4; итоговая приемка этого
> этапа относится к `337d0c6`. Приведенные ниже статусы, команды и числа
> описывают ту ревизию, а не текущий dual-stack. Актуальный результат:
> [интеграция dual-stack](DUAL-INTEGRATION.md) и
> [независимая приемка](../acceptance/dual-stack-acceptance.md).

Статус: done на основном выделенном стенде. Дата: 2026-10-01.
Независимая чистая приемка относится к MVP-06.

Кодовая ревизия последнего полного recovery/verify:
`ad27c75` (полный SHA в JSON evidence). Интегратор объединил отдельные
ветки MVP-01–04 через merge commits. Все живые изменения выполнял один оператор.

Реализованы `make tools/lint/bootstrap/health/deploy/verify/verify-idempotency/
verify-recovery/evidence`, один local environment, image post-renderer по
reviewed lock, явный kubeconfig и блокировка изменений. Verify создает JSON
при отказе и закрывает loopback port-forward. `PROFILE=final` честно не поддержан.

| Проверка | Результат | Доказательство |
| --- | --- | --- |
| Полный lint/render и негативные fixtures | pass | `make lint`; Helm всех компонентов, Python/YAML/Ansible, API/condition failures |
| Первый bootstrap, повтор, reboot | pass | MVP-01; DNS и межpod HTTP после reboot |
| Полный deploy | pass | Все workloads Ready, обе PVC Bound |
| Сквозной verify | pass | `20261001T112242Z-c174a32b`: readiness, conditions, HTTP, metrics, access/error, runtime |
| Request-ID и HTTP counter | pass | 24 успешных HTTP; scoped counter 1→25; тот же ID найден в access через Loki |
| Повторный deploy дважды | pass | `20261001T112340Z-bd51041c`: UIDs/PVC/secrets сохранены, оба verify pass |
| Prometheus/Loki persistence | pass | Исторический vector и log record найдены после замены pod |
| Fluentd positions/buffer | pass | `recovery-162280d8e552`: offsets сохранены, reread=0, все пять buffered markers доставлены |
| После восстановления | pass | `recovery-162280d8e552-after`, полный verify |
| Чистая независимая VM | отдельная карточка | MVP-06 |

Сводка и SHA256 исходных отчетов — [mvp-integration.json](../acceptance/mvp-integration.json).
Полные JSON находятся локально в `artifacts/`, без kubeconfig и secret payloads;
`make evidence` упаковывает только отчеты. Сырые Ansible/Helm журналы остаются
в игнорируемом `.state/`. README содержит команды и диагностику.

Во время интеграции временные файлы Ansible перенесены в `.state/ansible`;
убраны нерелевантные Helm NOTES выключенной Grafana. Проблем в живой
маршрутизации, metrics selectors или доставке логов не обнаружено.

Гарантии ограничены проверенной single-node конфигурацией и коротким outage.
Сетевые security-инварианты, TLS/canary, dashboards/alerts и hosted CI не
реализованы: это следующий этап, который пользователь пока не назначал.

После чистой приемки MVP-06 основной стенд возвращен в работу. Первый
health, вызванный до окончания загрузки, вернул ошибку на `kubelet=activating`;
служба перешла в active самостоятельно. Повторный health — pass (failed=0),
полный verify `20261001T115337Z-ec095221` — pass на чистой ревизии
`337d0c671617929ebf162e1216099524fe8d611d`. SHA256 полного JSON:
`caa733af19a0dd5d808cfab581c08a0f699f265959fc1766ed6cc165a065ef89`.
Приемочная VM штатно остановлена; ее диск и сырые доказательства сохранены.
