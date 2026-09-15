.PHONY: up down db-reset test lint eval demo-db demo

up:        ; docker compose up -d
down:      ; docker compose down
db-reset:  ; docker compose down -v && docker compose up -d
test:      ; uv run pytest
# format avant check : ruff check signale des lignes trop longues que le
# formateur aurait repliées, et le && arrêtait tout avant qu'il passe.
lint:      ; uv run ruff format . && uv run ruff check --fix .
eval:      ; uv run python scripts/evaluate.py

# ---------------------------------------------------------------------------
#  Démo — base séparée, chiffres fictifs, calcul réel (CLAUDE.md, « Démo »)
# ---------------------------------------------------------------------------

PSQL = docker compose exec -T -e PGOPTIONS=--client-min-messages=warning db \
       psql -U gramme -v ON_ERROR_STOP=1 -q

# Recrée gramme_demo de zéro : toutes les migrations, puis le seed fictif.
# Jamais la base principale. Relancer le jour de la démo : les prix sont datés
# par rapport à aujourd'hui, et price_drift ne regarde que les 42 derniers jours.
demo-db:
	@docker compose up -d --wait > /dev/null
	@$(PSQL) -d gramme -c "DROP DATABASE IF EXISTS gramme_demo WITH (FORCE)" -c "CREATE DATABASE gramme_demo"
	@for f in db/migrations/*.sql db/seed/demo.sql; do \
		$(PSQL) -d gramme_demo < $$f > /dev/null || exit 1; \
	done
	@echo "gramme_demo recréée : migrations + chiffres fictifs datés d'aujourd'hui"

# Base de démo fraîche, puis serveur ouvert au réseau local pour le téléphone.
demo: demo-db
	@echo ""
	@echo "  Sur cet ordinateur : http://localhost:8000"
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null); \
	if [ -n "$$ip" ]; then echo "  Sur le téléphone   : http://$$ip:8000"; fi
	@echo "  Réseau : partage de connexion du téléphone, jamais le Wi-Fi d'un établissement."
	@echo "  (la page d'envoi n'a pas d'authentification)"
	@echo ""
	uv run uvicorn gramme.web:app --host 0.0.0.0 --port 8000
