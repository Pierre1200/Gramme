"""Charge les ingrédients et recettes de l'établissement fictif.

    make synthetic-db

Lit `corpus/restaurant.toml` et l'écrit dans `gramme_synthetic`, jamais
ailleurs : des recettes inventées dans la base principale se mêleraient aux
fiches techniques d'un vrai client.

CLI mince : toute la logique est dans `gramme.referential`.
"""

import sys

import psycopg

from gramme.config import get_settings
from gramme.referential import ReferentialAlreadyLoaded, load_referential
from gramme.world import load_world

SYNTHETIC_DATABASE = "gramme_synthetic"


def main() -> int:
    url = get_settings().synthetic_database_url
    if not url:
        raise SystemExit("SYNTHETIC_DATABASE_URL manque dans .env (voir .env.example).")

    world = load_world()
    with psycopg.connect(url, connect_timeout=5) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        if database != SYNTHETIC_DATABASE:
            raise SystemExit(f"référentiel fictif : refus d'écrire dans « {database} »")
        try:
            loaded = load_referential(conn, world)
        except ReferentialAlreadyLoaded as e:
            raise SystemExit(f"{e} : recréer la base avec `make synthetic-db`") from e

    sellable = sum(recipe.is_sellable for recipe in world.recipes.values())
    print(
        f"Référentiel « {world.establishment.name} » chargé dans « {database} » : "
        f"{len(loaded.ingredient_ids)} ingrédients, {len(loaded.recipe_ids)} recettes "
        f"dont {sellable} vendues, {loaded.components} composants."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
