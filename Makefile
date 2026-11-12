.DEFAULT_GOAL := help
.PHONY: help up down db-reset test lint eval read demo-db demo corpus synthetic-db

help:      ## Liste les commandes disponibles
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sort | \
	awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

up:        ## Démarre PostgreSQL
	docker compose up -d
down:      ## Arrête PostgreSQL
	docker compose down
db-reset:  ## DÉTRUIT la base principale et rejoue les migrations
	docker compose down -v && docker compose up -d
test:      ## Lance les tests
	uv run pytest
# format avant check : ruff check signale des lignes trop longues que le
# formateur aurait repliées, et le && arrêtait tout avant qu'il passe.
lint:      ## Formate puis vérifie le code
	uv run ruff format . && uv run ruff check --fix .
# CORPUS=data/synthetic pour le jeu factice ; par défaut, les vraies factures.
CORPUS ?= data
read:      ## Lit les factures électroniques du dossier CORPUS
	uv run python scripts/read_invoices.py --corpus $(CORPUS)
eval:      ## Banc d'essai : lecture comparée au jeu de vérité
	uv run python scripts/evaluate.py --corpus $(CORPUS)
corpus:    ## Fabrique le jeu de factures factices Factur-X (demande Chrome)
	uv run python scripts/generate_corpus.py

# ---------------------------------------------------------------------------
#  Démo — base séparée, chiffres fictifs, calcul réel (CLAUDE.md, « Démo »)
# ---------------------------------------------------------------------------

PSQL = docker compose exec -T -e PGOPTIONS=--client-min-messages=warning db \
       psql -U gramme -v ON_ERROR_STOP=1 -q

demo-db:   ## Recrée gramme_demo avec des chiffres fictifs datés d'aujourd'hui
	@docker compose up -d --wait > /dev/null
	@$(PSQL) -d gramme -c "DROP DATABASE IF EXISTS gramme_demo WITH (FORCE)" -c "CREATE DATABASE gramme_demo"
	@for f in db/migrations/*.sql db/seed/demo.sql; do \
		$(PSQL) -d gramme_demo < $$f > /dev/null || exit 1; \
	done
	@echo "gramme_demo recréée : migrations + chiffres fictifs datés d'aujourd'hui"

demo: demo-db  ## Base de démo fraîche, puis les deux pages sur le réseau local
	@echo ""
	@echo "  Sur cet ordinateur : http://localhost:8000"
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null); \
	if [ -n "$$ip" ]; then echo "  Sur le téléphone   : http://$$ip:8000"; fi
	@echo "  Réseau : partage de connexion du téléphone, jamais le Wi-Fi d'un établissement."
	@echo "  (la page d'envoi n'a pas d'authentification)"
	@echo ""
	uv run uvicorn gramme.web:app --host 0.0.0.0 --port 8000

# Recrée gramme_synthetic de zéro (migrations, sans seed), y charge le
# référentiel de l'établissement fictif, puis ses factures de data/synthetic.
# Après `make corpus` : jamais la base principale.
synthetic-db:  ## Recrée gramme_synthetic et y écrit le jeu factice
	@docker compose up -d --wait > /dev/null
	@$(PSQL) -d gramme -c "DROP DATABASE IF EXISTS gramme_synthetic WITH (FORCE)" -c "CREATE DATABASE gramme_synthetic"
	@for f in db/migrations/*.sql; do \
		$(PSQL) -d gramme_synthetic < $$f > /dev/null || exit 1; \
	done
	uv run python scripts/load_referential.py
	uv run python scripts/store_invoices.py --corpus data/synthetic
