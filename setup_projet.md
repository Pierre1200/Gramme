# Setup du projet — environnement de travail

Objectif : être opérationnel en une heure, pas en trois semaines.
Tout ce qui n'est pas listé ici s'ajoute **quand le besoin apparaît**, jamais avant.

---

## 1. Outils système (une seule fois sur la machine)

```bash
# uv : remplace pip, venv, pyenv et poetry. Absurdement rapide.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Docker + Docker Compose : déjà en place depuis HBntory
docker --version && docker compose version
```

---

## 2. Création du projet

```bash
mkdir gramme && cd gramme
git init
uv init --package --name gramme
uv add pydantic pydantic-settings psycopg[binary] openai httpx python-dotenv
uv add --dev pytest ruff pre-commit
```

`uv` crée le `.venv` et le `pyproject.toml` tout seul.
Pour lancer quoi que ce soit : `uv run python ...` — pas besoin d'activer l'environnement.

---

## 3. Arborescence

Pensée pour ce projet précis, pas un squelette générique.
Note l'absence de `agents/` ou `graph.py` : c'est un pipeline, pas un système multi-agents.

```
gramme/
├── pyproject.toml
├── docker-compose.yml
├── .env.example              # commité (sans secrets)
├── .env                      # JAMAIS commité
├── .gitignore
├── Makefile
├── README.md
│
├── data/                     # ⚠ INTÉGRALEMENT gitignoré
│   ├── raw/                  # factures d'origine, telles que reçues
│   ├── truth/                # tes annotations manuelles (une par facture)
│   └── SOURCES.md            # d'où vient quoi, qui a donné son accord
│
├── db/
│   └── migrations/
│       └── 001_schema.sql    # le schéma coût matière
│
├── prompts/                  # versionnés dans Git : ce sont du code
│   ├── extraction.md
│   └── normalisation.md
│
├── src/gramme/
│   ├── __init__.py
│   ├── config.py             # settings via pydantic-settings
│   ├── models.py             # contrats Pydantic (Invoice, InvoiceLine…)
│   ├── llm.py                # client + log systématique tokens/coût/latence
│   ├── extract.py            # facture → JSON validé
│   ├── normalize.py          # libellé fournisseur → ingrédient du référentiel
│   ├── db.py                 # accès Postgres
│   └── report.py             # génération du rapport hebdo
│
├── scripts/
│   ├── ingest.py             # traite un dossier de factures
│   └── evaluate.py           # ⭐ compare extraction vs data/truth/
│
└── tests/
    ├── test_models.py
    └── test_cost.py          # vérifie recipe_cost() sur des cas connus
```

---

## 4. Fichiers de configuration

### `.gitignore`

```gitignore
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
.ruff_cache/

# Données clients — ne doit JAMAIS partir sur un remote
data/
*.pdf
*.jpg
*.jpeg
*.png
```

### `.env.example`

```bash
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3

DATABASE_URL=postgresql://gramme:gramme@localhost:5433/gramme

LOG_LEVEL=INFO
```

### `docker-compose.yml`

Postgres seul pour l'instant. Le reste s'ajoutera.
Port 5433 pour ne pas entrer en conflit avec le Postgres de HBntory.

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: gramme
      POSTGRES_PASSWORD: gramme
      POSTGRES_DB: gramme
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/migrations:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gramme"]
      interval: 5s
      retries: 5

volumes:
  pgdata:
```

### Ajout au `pyproject.toml`

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

### `.pre-commit-config.yaml`

Empêche de commiter du code cassé — ou un PDF client par accident.

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: detect-private-key
      - id: end-of-file-fixer
```

Activation : `uv run pre-commit install`

### `Makefile`

Une commande par action, pour ne pas réfléchir.

```makefile
.PHONY: up down db-reset test lint eval

up:        ; docker compose up -d
down:      ; docker compose down
db-reset:  ; docker compose down -v && docker compose up -d
test:      ; uv run pytest
lint:      ; uv run ruff check --fix . && uv run ruff format .
eval:      ; uv run python scripts/evaluate.py
```

---

## 5. Format d'annotation manuelle

Un fichier par facture dans `data/truth/`, nommé comme le document source.
`data/truth/2026-08-12_transgourmet_F44012.json` :

```json
{
  "source_file": "2026-08-12_transgourmet_F44012.pdf",
  "difficulty": "moyen",
  "notes": "Deuxième page mal scannée, coin plié",
  "supplier": "Transgourmet",
  "invoice_number": "F44012",
  "invoice_date": "2026-08-12",
  "total_ht": 412.55,
  "lines": [
    {
      "raw_label": "BEURRE DOUX PLAQUE 25KG ELLE&VIRE",
      "qty": 1,
      "unit": "colis",
      "pack_qty_g": 25000,
      "unit_price_ht": 219.00,
      "line_total_ht": 219.00
    }
  ]
}
```

Le champ `difficulty` (`facile` / `moyen` / `difficile`) te permettra de
mesurer où le système échoue, pas seulement s'il échoue.

---

## 6. Ordre de développement

Strictement dans cet ordre. Ne saute pas d'étape.

1. **`models.py`** — les contrats Pydantic. Ils définissent ce qu'est une
   facture valide avant qu'aucun modèle ne soit appelé.
2. **`scripts/evaluate.py`** — le banc d'essai, **avant** l'extracteur.
   Il compare une sortie à `data/truth/` et affiche un score par champ.
   Écrire le test avant le code force à définir ce que « réussir » veut dire.
3. **`llm.py`** — client + logging systématique (modèle, tokens in/out,
   coût, durée). Aucun appel LLM ne doit exister hors de ce module.
4. **`extract.py`** — la première vraie tentative. Tu lances `make eval`.
   Tu obtiens un chiffre. Tu itères sur le prompt, pas sur le code.
5. **Le schéma SQL** et `db.py`, une fois l'extraction correcte.
6. **`normalize.py`**, puis `report.py`.

---

## 7. À ajouter plus tard, quand le besoin sera réel

| Outil | Quand l'ajouter |
|---|---|
| Observabilité LLM (Langfuse) | Dès que le pipeline dépasse 2 appels enchaînés |
| GitHub Actions | Dès qu'il y a des tests qui valent la peine d'être lancés |
| Traefik / Caddy | Le jour du premier déploiement chez un client |
| File d'attente (table `job`) | Quand le traitement synchrone devient trop lent |
| LangGraph ou équivalent | Seulement si tu as besoin de reprendre un traitement à mi-parcours après un crash |
| Sauvegardes automatisées | **Avant** le premier client payant, pas après |

---

## Rappel

Le setup n'est pas le projet. Si au bout d'une journée tu n'as pas encore
regardé une facture, tu es en train de procrastiner avec du YAML.
