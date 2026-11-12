"""Le référentiel chargé doit être celui du fichier, au gramme près.

Le test central fait parler deux moteurs écrits séparément : le calcul de coût
en SQL et les besoins bruts du simulateur en Python. S'ils s'accordent sur les
22 produits vendus, rendements et sous-recettes sont chargés juste.
"""

from decimal import Decimal

import psycopg
import pytest

from gramme.config import get_settings
from gramme.referential import ReferentialAlreadyLoaded, load_referential
from gramme.simulation import gross_needs
from gramme.world import load_world


@pytest.fixture(scope="module")
def world():
    return load_world()


@pytest.fixture
def conn():
    try:
        connection = psycopg.connect(get_settings().database_url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres injoignable : lancer `make up`")
    with connection, connection.transaction(force_rollback=True):
        yield connection


@pytest.fixture
def loaded(conn, world):
    return load_referential(conn, world)


def scalar(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_everything_in_the_file_is_loaded(conn, world, loaded):
    assert scalar(conn, "SELECT count(*) FROM ingredient") == len(world.ingredients) == 57
    assert scalar(conn, "SELECT count(*) FROM recipe") == len(world.recipes) == 38
    assert scalar(conn, "SELECT count(*) FROM recipe WHERE is_sellable") == 22
    assert scalar(conn, "SELECT count(*) FROM recipe_component") == loaded.components
    assert loaded.components == sum(len(r.components) for r in world.recipes.values())


def test_a_sub_recipe_carries_no_selling_fields(conn, loaded):
    row = conn.execute(
        "SELECT portions, selling_price_ttc, vat_rate FROM recipe WHERE id = %s",
        (loaded.recipe_ids["pate_sucree"],),
    ).fetchone()
    assert row == (None, None, None)


def test_vat_is_stored_as_a_fraction(conn, loaded):
    rates = {
        key: scalar(conn, "SELECT vat_rate FROM recipe WHERE id = %s", loaded.recipe_ids[key])
        for key in ("paella", "croissant")
    }
    assert rates == {"paella": Decimal("0.100"), "croissant": Decimal("0.055")}


def test_the_sql_cost_engine_agrees_with_the_simulator(conn, world, loaded):
    # Un euro par unité de base pour chaque ingrédient : le coût d'une portion
    # devient alors la somme de ses besoins bruts, que le simulateur connaît.
    supplier = scalar(conn, "INSERT INTO supplier (name) VALUES ('test') RETURNING id")
    for ingredient_id in loaded.ingredient_ids.values():
        product = scalar(
            conn,
            "INSERT INTO supplier_product (supplier_id, raw_label, ingredient_id, status)"
            " VALUES (%s, %s, %s, 'confirmed') RETURNING id",
            supplier,
            f"TEST {ingredient_id}",
            ingredient_id,
        )
        conn.execute(
            "INSERT INTO price_point (supplier_product_id, observed_on, price_per_base_unit)"
            " VALUES (%s, CURRENT_DATE, 1)",
            (product,),
        )

    needs = gross_needs(world)
    for key, recipe in world.recipes.items():
        if not recipe.is_sellable:
            continue
        cost = scalar(conn, "SELECT recipe_cost(%s)", loaded.recipe_ids[key])
        expected = sum(needs[key].values())
        assert cost / recipe.portions == pytest.approx(expected, rel=Decimal("1e-9")), key


def test_a_second_load_is_refused(conn, world, loaded):
    with pytest.raises(ReferentialAlreadyLoaded):
        load_referential(conn, world)
    assert scalar(conn, "SELECT count(*) FROM ingredient") == 57
