# Приемка назначенных FINAL-01 и FINAL-05

**Статус: done в назначенной области, 2 октября 2026 года.** TLS/Gateway,
canary с автооткатом и восстановление проверены на выделенной Ubuntu VM.
Итоговый код: `9ce5f99d92aca36cf2399469962a710f9b444def`.
[Машинный протокол](../acceptance/final-01-05.json),
[независимое ревью](FINAL-REVIEW.md).

Полный FINAL не принимается: Grafana/alerts и alert recovery (FINAL-02),
NetworkPolicy (FINAL-03), CI (FINAL-04), новая чистая установка и упаковка
(FINAL-07) не назначены. `PROFILE=final` остается неподдерживаемым.
Прежняя чистая приемка MVP относится к `cc3afc6`, аудит — к `8ed0e70`.

## Результат и проверки

Два изолированных backend обслуживают host/path routes через HTTP и HTTPS
по IPv6/IPv4. Stable имеет две replicas, canary — одну. TLS проверяет локальную
CA и hostname; повтор deploy сохраняет Secret. Выпуск проверяет точный HTTP
ответ и свежие метрики отдельного backend за ограниченное окно. Отказ или
недостаток данных возвращает 100/0 и здоровую конфигурацию кандидата.

| Проверка и команда | Результат live, если не указано другое |
| --- | --- |
| `make lint` на Ubuntu VM | pass; 167 unit/fixture tests, Helm lint/render, Ansible/Python syntax, image lock |
| `make verify-canary ENV=ubuntu` | pass; 6 сценариев и cleanup |
| Здоровый canary | 2400 HTTP за 60 с: 2160 stable, 240 canary; backend delta 240, 5xx 0% |
| HTTP 500 после 90/10 | rejected; ошибочные ответы именно кандидата, backend 5xx 100%; rollback pass |
| Неверное тело после 90/10 | rejected по HTTP при 0% 5xx; rollback pass |
| Неверное тело до 90/10 | rejected до включения кандидата в общий трафик; rollback pass |
| Недоступные метрики | реальный port-forward остановлен; inconclusive, rollback pass |
| Недостаточная выборка | 20 HTTP, из них 2 canary; backend delta 3 < 100; inconclusive, rollback pass |
| `scripts/canary.py --env ubuntu --revision cli-negative-72fb320310fc --fault-mode wrong-body` | настоящий subprocess exit 2, rejected, rollback pass |
| `make verify-release ENV=ubuntu` при 90/10 и после 100/0 | pass; MVP 11 основных/105 вложенных и Gateway/TLS 154 checks |
| Распределение 90/10 | по 1000 запросов без retries: canary IPv6 99/1000, IPv4 100/1000; ошибок 0 |
| TLS negative | неверные hostname и CA отклонены по обеим семьям; TLS verification включена |
| `make verify-interruption ENV=ubuntu` | pass; реальные pending-upgrade, same-run exit 2 и next-run writer exit 143; 100/0 восстановлено, сертификат сохранен |
| `make demo ENV=ubuntu DEMO_ARGS="--section app"` | pass; замена pod: 86 запросов, 0 ошибок; новая конфигурация и Helm rollback подтверждены ответами |
| `make demo ENV=ubuntu DEMO_ARGS="--section recovery"` после rollout | pass; история Prometheus/Loki сохранена, все 5 buffer markers найдены по одному за 49,23 с |
| Позиции Fluentd | все 3 активных источника/inode/размера/offset сохранены; replay 0; очищены только 5 неактивных записей |
| `make verify-idempotency ENV=ubuntu` | pass; два deploy/verify, 28 ресурсов: UIDs, PVC bindings и содержимое secrets сохранены |
| Внешне управляемый reboot | pass; новый boot ID, API ready через 33,31 с; затем health и verify-release pass |
| `scripts/check-gateway.final.py --env ubuntu --requests 100 --run-id external-final-01-05 --output artifacts/release/external-final-01-05/report.json` с рабочей машины | pass; 154 checks, по 100 stable HTTP на семью, TLS и negative cases |

Все пять отрицательных canary-сценариев дали 25/25 stable ответов и 5/5 ответов
восстановленного кандидата. Сам откат занимал 12,426–14,525 с; это не время
от первой ошибки до решения. Непрерывный rollout controller не заявляется.
Метрики включают пробы ожидания конфигурации, поэтому их прирост может
отличаться от числа запросов измеренного окна.

Шесть сценариев вызывают production `run_locked` внутри процесса; их поле
exit code выведено из результата. Настоящий exit CLI отдельно проверен
subprocess-запуском и сценарием interruption.

## Ревизии, границы и evidence

Canary/TLS suite принят на clean `da0a0af`, interruption — на clean `b45f6a6`,
последние app/recovery/idempotency/CLI/reboot — на clean `9ce5f99`.
Production charts/config/deploy/canary, HTTP/metrics gate и MVP/TLS verifier
между этими ревизиями идентичны. Пути сравнения и SHA-256 18 исходных отчетов
записаны в JSON. Менялись demo/recovery harness, fixtures и сбор evidence;
затронутые сценарии повторялись.

Demo принят отдельными секциями; единого успешного `make demo --reboot` не
заявляем. Внешний supervisor `.state/final/reboot.py` вызвал штатную функцию
`reboot()` из `release-demo.py`, затем health и verify на VM. Это позволило
пережить reboot и обойти timeout IPv6 loopback на рабочей машине. Штатная
команда для внешнего controller: `make demo ENV=ubuntu DEMO_ARGS="--section reboot"`.
Запуск reboot с целевой VM отклоняется до изменений. Полный lint рабочей
машины также не заявляется из-за loopback fixtures; тот же `make lint` прошел на VM.

Итоговый стенд: stable 100%, canary 0%, оба backend здоровы, faultMode none.
Старый checkout сохранен; проверялся отдельный checkout. Использовались
только явный kubeconfig и выделенная VM.

Архив `artifacts/evidence/20261002T142057Z.tar.gz` содержит успешные и неуспешные
JSON reports, вложенные canary reports и подробные gateway.json. Ключи,
kubeconfig и сырые логи в него не входят. Хеш архива — в машинном протоколе;
публичный протокол не содержит реальных адресов.

## Исправления, найденные живой приемкой

Ранние неуспешные отчеты сохранены и не переименованы в успешные:

- Проверка attachedRoutes согласована с поведением закрепленного Traefik при
  двух parentRefs; точные четыре route, их parents и текущие conditions остаются обязательными.
- Hostname HTTPS listener давал одинаковый приоритет generic/host route.
  Имя осталось на HTTPRoute и сертификате; TLS host routing повторно проверен.
- Rolling canary смешивал старую и новую версии после Helm readiness.
  Canary использует Recreate при нулевом весе; stable сохраняет две replicas
  и rolling update. Ожидание версии отделено от строгого smoke.
- Conditions HTTPRoute опережали фактическое применение весов. Теперь перед
  smoke проверяется X-Traffic-Split обоих маршрутов; измеряемый трафик остается без retries.
- Полное равенство position file Fluentd ошибочно запрещало очистку старых
  записей после rollout. Воспроизведено 8 → 3: удалены только 5 unwatched,
  активные источники неизменны, replay 0. Такое поведение соответствует
  [Fluentd 1.19.0](https://raw.githubusercontent.com/fluent/fluentd/v1.19.0/lib/fluent/plugin/in_tail/position_file.rb).
  Проверка требует сохранения всех активных источников/offsets, marker boundaries
  и replay 0; допускает только удаление доказанно неактивной истории.
  Добавлены 20 негативных тестов; новый rollout/recovery прошел.
