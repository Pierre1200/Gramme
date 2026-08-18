# Gramme

Coût matière pour la restauration et les métiers de bouche.

Lecture automatisée de factures fournisseurs → historique de prix →
coûts matière (parage et rendements compris) → rapport hebdomadaire.

## Démarrer

```bash
# uv (une seule fois sur la machine)
brew install uv

uv sync                 # crée .venv et installe les dépendances
cp .env.example .env    # puis renseigne MINIMAX_API_KEY
make up                 # Postgres sur le port 5433, schéma appliqué au 1er boot
```

## Commandes

| Commande | Effet |
|---|---|
| `make up` / `make down` | démarre / arrête Postgres |
| `make db-reset` | détruit le volume et rejoue `db/migrations/` |
| `make test` | pytest |
| `make lint` | ruff check --fix + format |
| `make eval` | banc d'essai extraction vs `data/truth/` |

## Structure

```
db/migrations/001_schema.sql   schéma coût matière (sous-recettes récursives)
src/gramme/models.py           contrats Pydantic
data/                          factures clients — INTÉGRALEMENT gitignoré
```

## Règles

- Le LLM extrait et rédige. **SQL calcule.** Jamais un montant produit par un modèle.
- Historique de prix append-only : aucun `UPDATE` sur un prix.
- Rien sous `data/` ne part sur un remote.

Détail des décisions d'architecture : `CLAUDE.md`.
