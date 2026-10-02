ENV ?= ubuntu
PROFILE ?= mvp
PYTHON := $(CURDIR)/.venv/bin/python
export PATH := $(CURDIR)/.cache/tools:$(CURDIR)/.venv/bin:$(PATH)
export ANSIBLE_LOCAL_TEMP := $(CURDIR)/.state/ansible/tmp
export ANSIBLE_SSH_CONTROL_PATH_DIR := $(CURDIR)/.state/ansible/cp

.PHONY: tools lint bootstrap health deploy verify verify-idempotency verify-recovery evidence deploy-canary restore-stable verify-canary verify-release demo
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

deploy-canary:
	$(PYTHON) scripts/canary.py --env $(ENV) $(CANARY_ARGS)

restore-stable:
	$(PYTHON) scripts/canary.py --env $(ENV) --action restore

verify-canary:
	$(PYTHON) tests/canary/verify.py --env $(ENV)

verify-release:
	$(PYTHON) scripts/verify-release.py --env $(ENV)

demo:
	$(PYTHON) tests/e2e/release-demo.py --env $(ENV) $(DEMO_ARGS)
