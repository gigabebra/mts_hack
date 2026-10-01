ENV ?= ubuntu
PROFILE ?= mvp
PYTHON := $(CURDIR)/.venv/bin/python
export PATH := $(CURDIR)/.cache/tools:$(CURDIR)/.venv/bin:$(PATH)
export ANSIBLE_LOCAL_TEMP := $(CURDIR)/.state/ansible/tmp
export ANSIBLE_SSH_CONTROL_PATH_DIR := $(CURDIR)/.state/ansible/cp

.PHONY: tools lint bootstrap health deploy verify verify-idempotency verify-recovery evidence
tools:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt
	$(PYTHON) scripts/tools.py

lint:
	$(PYTHON) scripts/lint.py

bootstrap:
	$(PYTHON) scripts/bootstrap.py --env $(ENV)

health:
	$(PYTHON) scripts/bootstrap.py --env $(ENV) --health-only

deploy:
	$(PYTHON) scripts/deploy.py --env $(ENV)

verify:
	$(PYTHON) scripts/verify.py --env $(ENV) --profile $(PROFILE)

verify-idempotency:
	$(PYTHON) scripts/verify-idempotency.py --env $(ENV)

verify-recovery:
	$(PYTHON) tests/e2e/mvp-recovery.py --env $(ENV)

evidence:
	$(PYTHON) scripts/evidence.py --env $(ENV)
