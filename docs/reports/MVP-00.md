# MVP-00

Статус: done. Дата: 2026-10-01. Runtime-приемка матрицы ожидает MVP-05/06.

Созданы AGENTS.md, docs/contracts.md, docs/status.md, config/versions.yaml,
config/images.lock.yaml, .gitignore и ручной инструмент обновления image lock.
Исходные планы сохранены. Единственный оператор общей VM — интегратор;
компонентные worktree создаются от этой стартовой ревизии на main.

| Проверка | Результат | Доказательство |
| --- | --- | --- |
| Источники charts и binaries | pass | Загружены принятые версии; SHA256 записаны в versions.yaml |
| Transitive images | pass | Render минимального kube-prometheus-stack и Calico; 20 OCI digests, включая Operator reloader и kubeadm images |
| Kubernetes/Gateway/Calico compatibility | pass, documentation | Traefik 3.6 поддерживает Gateway 1.4; Calico 3.31 тестирует Kubernetes 1.32–1.35 |
| Fluentd и plugin | pass | Изолированный docker run: Fluentd 1.19.0; Ruby загрузил out_loki из /fluentd/plugins и regexp/JSON parsers |
| Стенд | pass, prerequisites | SSH к выделенному гипервизору доступен; проверены свободные VMID, RAM и local thin storage; две отдельные Ubuntu VM |
| Runtime Kubernetes | not run | Выполняется после MVP-01–04 интегратором |

Временный cloud image Ubuntu 24.04 закрепляется SHA256 в локальном inventory;
эксперт использует любую чистую Ubuntu 24.04 с SSH/sudo. Реальные адреса и
параметры гипервизора не входят в Git. Публичный хостинг по умолчанию GitHub;
публикация не выполнена и не нужна для разработки MVP.

Начальная проверка plugin через gem load path не нашла его: image хранит
plugin отдельным файлом. Повтор с `ruby -I /fluentd/plugins -r out_loki`
успешен; обязательный plugin path внесен в контракт.
