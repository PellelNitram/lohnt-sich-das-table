PORT ?= 8000
PYTHON ?= python3

.PHONY: build
build: ## Build ./dist from data/sample.csv (fake data, safe for local work)
	@$(PYTHON) scripts/build_site.py --sample

.PHONY: build-live
build-live: ## Build ./dist from the real sheet (needs SHEET_CSV_URL, e.g. in .env)
	@set -a; [ -f .env ] && . ./.env; set +a; $(PYTHON) scripts/build_site.py

.PHONY: serve
serve: build ## Build with sample data and serve on http://localhost:$(PORT)
	@echo "Serving dist/ at http://localhost:$(PORT) (Ctrl+C to stop)"
	@cd dist && $(PYTHON) -m http.server $(PORT)

.PHONY: serve-live
serve-live: build-live ## Build with the real sheet and serve on http://localhost:$(PORT)
	@echo "Serving dist/ at http://localhost:$(PORT) (Ctrl+C to stop)"
	@cd dist && $(PYTHON) -m http.server $(PORT)

.PHONY: test
test: ## Run the unit tests
	@$(PYTHON) -m unittest discover -s tests -v

.PHONY: clean
clean: ## Remove ./dist
	@rm -rf dist

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
