# Sesharo Home Assistant integration — dev + release tasks.
# HA isn't installed in this repo, so `test`/`check` exercise the pure logic + syntax only
# (see AGENTS.md → Tests for the on-device verification gap).

REPO    := Sesharo/sesharo-homeassistant
PKG     := custom_components/sesharo
VERSION := $(shell python3 -c "import json; print(json.load(open('$(PKG)/manifest.json'))['version'])")
TAG     := v$(VERSION)

PYTHON  ?= python3

.PHONY: help test smoke lint format coverage check install-dev release version

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

version: ## Print the version from manifest.json
	@echo $(VERSION)

install-dev: ## Create .venv and install the test/dev toolchain (pytest + HA + ruff)
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -r requirements_test.txt
	@echo "Activate with:  . .venv/bin/activate"

test: ## Run the full pytest suite (needs the dev deps — see install-dev)
	$(PYTHON) -m pytest

smoke: ## Fast, dependency-free logic tests (units + discovery) — no HA/pytest needed
	@$(PYTHON) tests/test_units.py
	@$(PYTHON) tests/test_discovery.py

lint: ## ruff lint + format check (no changes made)
	$(PYTHON) -m ruff check custom_components tests
	$(PYTHON) -m ruff format --check custom_components tests

format: ## Apply ruff auto-formatting
	$(PYTHON) -m ruff format custom_components tests

coverage: ## Full suite with a coverage report
	$(PYTHON) -m pytest --cov --cov-report=term-missing

check: ## Syntax-check all Python + the panel JS (a build proxy `pytest` can't cover)
	@python3 -m py_compile $(PKG)/*.py && echo "python: ok"
	@node --check $(PKG)/www/sesharo-panel.js && echo "panel js: ok"
	@python3 -c "import json; [json.load(open(f)) for f in ['$(PKG)/manifest.json', '$(PKG)/strings.json', '$(PKG)/translations/en.json', 'hacs.json']]" && echo "json: ok"
	@diff -q $(PKG)/strings.json $(PKG)/translations/en.json >/dev/null \
		&& echo "translations: in sync" \
		|| (echo "translations/en.json is out of sync with strings.json"; exit 1)

# Cut a GitHub release for the current manifest version. HACS switches from branch-tracking to
# release-tracking once releases exist, giving a real "Update available" card + changelog + version.
# Notes come from the matching `## $(TAG)` section of CHANGELOG.md. Requires `gh` auth + push access.
# Depends only on the dependency-free gates (check + smoke) so it runs anywhere with `gh` — no venv
# needed. The authoritative full pytest suite + ruff lint run in CI on every push to main; cut a
# release from a green main.
release: check smoke ## Tag + publish a GitHub release for the current version (notes from CHANGELOG.md)
	@command -v gh >/dev/null || (echo "gh CLI not found — install + 'gh auth login'"; exit 1)
	@git rev-parse --abbrev-ref HEAD | grep -qx main \
		|| (echo "Refusing to release from a non-main branch (on $$(git rev-parse --abbrev-ref HEAD))."; exit 1)
	@test -z "$$(git status --porcelain)" \
		|| (echo "Working tree is dirty — commit before releasing."; exit 1)
	@gh release view $(TAG) --repo $(REPO) >/dev/null 2>&1 \
		&& (echo "Release $(TAG) already exists — bump manifest.json 'version' first."; exit 1) || true
	@awk '/^## $(TAG) /{f=1;print;next} /^## v/{if(f)exit} f' CHANGELOG.md > .release-notes.tmp
	@test -s .release-notes.tmp \
		|| (echo "No '## $(TAG)' section in CHANGELOG.md."; rm -f .release-notes.tmp; exit 1)
	gh release create $(TAG) --repo $(REPO) --title "$(TAG)" --notes-file .release-notes.tmp
	@rm -f .release-notes.tmp
	@echo "Published $(TAG). Users will see it in HACS → restart HA to load it."
