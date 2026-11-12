"""Le référentiel de l'établissement fictif, chargé dans le schéma.

Le référentiel, ce sont les ingrédients et les recettes : ce à quoi la
normalisation rattache les produits fournisseurs, et ce dont le rapport calcule
le coût. Ici il vient de `corpus/restaurant.toml` ; chez un vrai client, il
viendra de ses fiches techniques.

Ce module n'écrit ni prix ni rattachement : seulement ce que l'établissement
cuisine, et comment.
"""

from dataclasses import dataclass

import psycopg

from gramme.world import World


class ReferentialAlreadyLoaded(RuntimeError):
    """La base contient déjà des ingrédients.

    Un second chargement doublerait tout le référentiel sans erreur visible :
    la base se recrée, elle ne se complète pas.
    """


@dataclass(frozen=True)
class LoadedReferential:
    """Identifiants attribués par la base, indexés par les clés du fichier."""

    ingredient_ids: dict[str, int]
    recipe_ids: dict[str, int]
    components: int


def load_referential(conn: psycopg.Connection, world: World) -> LoadedReferential:
    """Écrit ingrédients, recettes et composants, tout ou rien."""
    with conn.transaction(), conn.cursor() as cur:
        if cur.execute("SELECT 1 FROM ingredient LIMIT 1").fetchone():
            raise ReferentialAlreadyLoaded("la base contient déjà un référentiel")

        ingredient_ids = {
            key: cur.execute(
                "INSERT INTO ingredient (name, base_unit) VALUES (%s, %s) RETURNING id",
                (ingredient.name, ingredient.unit),
            ).fetchone()[0]
            for key, ingredient in world.ingredients.items()
        }

        # Toutes les recettes d'abord : un composant peut citer une sous-recette
        # déclarée plus loin dans le fichier.
        recipe_ids = {
            key: cur.execute(
                # Le fichier écrit la TVA en pourcentage, le schéma en fraction :
                # la conversion se fait en SQL, comme tout calcul.
                """
                INSERT INTO recipe (name, yield_qty, yield_unit, is_sellable,
                                    portions, selling_price_ttc, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, %s::NUMERIC / 100)
                RETURNING id
                """,
                (
                    recipe.name,
                    recipe.batch.qty,
                    recipe.batch.unit,
                    recipe.is_sellable,
                    recipe.portions,
                    recipe.price_ttc,
                    # Une sous-recette n'est pas vendue : pas de TVA, plutôt que
                    # le 5,5 % par défaut du schéma.
                    recipe.vat,
                ),
            ).fetchone()[0]
            for key, recipe in world.recipes.items()
        }

        components = 0
        for key, recipe in world.recipes.items():
            for component in recipe.components:
                cur.execute(
                    """
                    INSERT INTO recipe_component (parent_recipe_id, ingredient_id,
                                                  child_recipe_id, qty, unit,
                                                  yield_ratio, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        recipe_ids[key],
                        ingredient_ids.get(component.ingredient),
                        recipe_ids.get(component.recipe),
                        component.qty,
                        component.unit,
                        component.yield_ratio,
                        component.note,
                    ),
                )
                components += 1

    return LoadedReferential(ingredient_ids, recipe_ids, components)
