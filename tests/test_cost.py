"""Le moteur de calcul, testé dans Postgres lui-même.

Décision 2 : tous les coûts, marges et écarts sont calculés en SQL. Ces tests
sont donc les seuls à vérifier les chiffres que le client lira.

Chaque test travaille dans une transaction annulée à la fin : la base reste
vide, et les tests ne dépendent pas du seed de démonstration. Ignorés si
Postgres ne répond pas (`make up`).
"""

from decimal import Decimal

import psycopg
import pytest

from gramme.config import get_settings


@pytest.fixture
def cur():
    try:
        connection = psycopg.connect(get_settings().database_url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres injoignable : lancer `make up`")
    with connection:
        with connection.cursor() as cursor:
            yield cursor
        connection.rollback()  # rien ne survit au test


def insert(cur, sql: str, *params) -> int:
    cur.execute(sql + " RETURNING id", params)
    return cur.fetchone()[0]


def ingredient_priced_at(cur, name: str, price_per_gram: str, days_ago: int = 0) -> int:
    """Crée un ingrédient, son produit fournisseur confirmé et un relevé de prix."""
    supplier = insert(cur, "INSERT INTO supplier (name) VALUES ('test')")
    ingredient = insert(cur, "INSERT INTO ingredient (name, base_unit) VALUES (%s, 'g')", name)
    insert(
        cur,
        "INSERT INTO supplier_product (supplier_id, raw_label, ingredient_id, status)"
        " VALUES (%s, %s, %s, 'confirmed')",
        supplier,
        name.upper(),
        ingredient,
    )
    # L'identifiant de l'INGRÉDIENT, pas celui du produit fournisseur. Les deux
    # coïncident sur une base neuve puis divergent : les séquences Postgres ne
    # sont pas annulées par un ROLLBACK. Ce bug a fait passer des tests par hasard.
    add_price(cur, ingredient, price_per_gram, days_ago)
    return ingredient


def add_price(cur, ingredient: int, price_per_gram: str, days_ago: int) -> None:
    """Ajoute un relevé sur le produit fournisseur de cet ingrédient."""
    cur.execute(
        "INSERT INTO price_point (supplier_product_id, observed_on, price_per_base_unit)"
        " SELECT id, CURRENT_DATE - %s, %s FROM supplier_product WHERE ingredient_id = %s",
        (days_ago, Decimal(price_per_gram), ingredient),
    )


def recipe(
    cur, name: str, yield_qty: int = 1, portions: int | None = None, price: str | None = None
) -> int:
    return insert(
        cur,
        "INSERT INTO recipe (name, yield_qty, yield_unit, is_sellable, portions,"
        " selling_price_ttc, vat_rate) VALUES (%s, %s, 'g', %s, %s, %s, 0.100)",
        name,
        yield_qty,
        price is not None,
        portions,
        price,
    )


def component(cur, parent: int, qty: int, *, ingredient=None, child=None, ratio="1.0") -> None:
    cur.execute(
        "INSERT INTO recipe_component (parent_recipe_id, ingredient_id, child_recipe_id,"
        " qty, unit, yield_ratio) VALUES (%s, %s, %s, %s, 'g', %s)",
        (parent, ingredient, child, qty, Decimal(ratio)),
    )


def cost(cur, recipe_id: int, days_ago: int | None = None) -> Decimal:
    if days_ago is None:
        cur.execute("SELECT recipe_cost(%s)", (recipe_id,))
    else:
        cur.execute("SELECT recipe_cost_at(%s, CURRENT_DATE - %s)", (recipe_id, days_ago))
    return cur.fetchone()[0].quantize(Decimal("0.01"))


class TestRecipeCost:
    def test_the_yield_ratio_is_applied_to_the_raw_quantity(self, cur):
        # 2 250 g de gambas décortiquées à rendement 0,50 = 4 500 g achetés.
        prawns = ingredient_priced_at(cur, "gambas", "0.0168")
        paella = recipe(cur, "paella")
        component(cur, paella, 2250, ingredient=prawns, ratio="0.50")
        assert cost(cur, paella) == Decimal("75.60")

    def test_the_cost_flows_through_a_sub_recipe(self, cur):
        # La tarte utilise 400 g d'une pâte dont le batch fait 1 000 g : elle
        # porte 40 % du coût du batch.
        butter = ingredient_priced_at(cur, "beurre", "0.008")
        pastry = recipe(cur, "pâte", yield_qty=1000)
        component(cur, pastry, 500, ingredient=butter)  # batch : 4,00 €
        tart = recipe(cur, "tarte")
        component(cur, tart, 400, child=pastry)
        assert cost(cur, tart) == Decimal("1.60")

    def test_todays_cost_equals_the_cost_at_todays_date(self, cur):
        butter = ingredient_priced_at(cur, "beurre", "0.008")
        dish = recipe(cur, "plat")
        component(cur, dish, 100, ingredient=butter)
        cur.execute("SELECT recipe_cost(%s) = recipe_cost_at(%s, CURRENT_DATE)", (dish, dish))
        assert cur.fetchone()[0] is True


class TestCostAtADate:
    def test_it_reads_the_price_in_force_at_that_date(self, cur):
        butter = ingredient_priced_at(cur, "beurre", "0.00742", days_ago=42)
        add_price(cur, butter, "0.00876", days_ago=3)
        dish = recipe(cur, "croissants")
        component(cur, dish, 1000, ingredient=butter)
        assert cost(cur, dish, days_ago=42) == Decimal("7.42")
        assert cost(cur, dish) == Decimal("8.76")

    def test_it_ignores_later_prices(self, cur):
        butter = ingredient_priced_at(cur, "beurre", "0.008", days_ago=10)
        dish = recipe(cur, "plat")
        component(cur, dish, 1000, ingredient=butter)
        assert cost(cur, dish, days_ago=42) == Decimal("0.00")


class TestKnownLimit:
    def test_an_ingredient_without_a_price_vanishes_silently(self, cur):
        """Verrouille un défaut ACTUEL, documenté dans CLAUDE.md.

        La jointure interne fait disparaître un ingrédient sans prix : le coût
        est sous-estimé sans aucun avertissement. Le jour où c'est corrigé, ce
        test doit casser et être réécrit — c'est voulu.
        """
        butter = ingredient_priced_at(cur, "beurre", "0.008")
        unpriced = insert(cur, "INSERT INTO ingredient (name, base_unit) VALUES ('safran', 'g')")
        dish = recipe(cur, "plat")
        component(cur, dish, 1000, ingredient=butter)
        component(cur, dish, 1, ingredient=unpriced)
        assert cost(cur, dish) == Decimal("8.00")  # le safran n'est pas compté


class TestReportViews:
    def test_the_gross_margin_in_euros(self, cur):
        # 12 € TTC à 10 % = 10,91 € HT ; coût 3,60 € par part -> 7,31 €.
        rice = ingredient_priced_at(cur, "riz", "0.0036")
        paella = recipe(cur, "paella", portions=1, price="12.00")
        component(cur, paella, 1000, ingredient=rice)
        cur.execute("SELECT gross_margin_ht FROM recipe_margin WHERE id = %s", (paella,))
        assert cur.fetchone()[0] == Decimal("7.31")

    def test_the_ratio_drift_over_six_weeks(self, cur):
        butter = ingredient_priced_at(cur, "beurre", "0.00742", days_ago=42)
        add_price(cur, butter, "0.00876", days_ago=3)
        dish = recipe(cur, "croissant", portions=1, price="11.00")  # 10,00 € HT
        component(cur, dish, 1000, ingredient=butter)
        cur.execute(
            "SELECT ratio, ratio_before, drift_points, level FROM report_margins"
            " WHERE recipe_id = %s",
            (dish,),
        )
        assert cur.fetchone() == (Decimal("87.6"), Decimal("74.2"), Decimal("13.4"), "alerte")

    def test_an_inconsistent_invoice_total_is_flagged(self, cur):
        supplier = insert(cur, "INSERT INTO supplier (name) VALUES ('marée (test)')")
        for number, total in (("OK", "100.04"), ("KO", "104.20")):
            invoice = insert(
                cur,
                "INSERT INTO invoice (supplier_id, invoice_number, invoice_date, total_ht,"
                " source_uri, source_hash) VALUES (%s, %s, CURRENT_DATE, %s, 'test', %s)",
                supplier,
                number,
                Decimal(total),
                f"hash-{number}",
            )
            cur.execute(
                "INSERT INTO invoice_line (invoice_id, raw_label, qty, unit_price_ht,"
                " line_total_ht) VALUES (%s, 'BAR', 1, 100, 100)",
                (invoice,),
            )
        cur.execute(
            "SELECT label, gap FROM report_flags WHERE reason = 'total_mismatch'"
            " AND supplier = 'marée (test)'"
        )
        # 0,04 € d'écart reste sous la tolérance ; 4,20 € la dépasse.
        assert cur.fetchall() == [("KO", Decimal("4.20"))]

    def test_the_tile_counts_alerts_only(self, cur):
        """Décision du 15/09 : seules les alertes comptent en en-tête.

        La vigilance reste visible plat par plat. Mesuré en différence avant et
        après insertion, pour ne pas dépendre du contenu de la base.
        """
        cur.execute("SELECT dishes_on_alert FROM report_summary")
        before = cur.fetchone()[0]

        butter = ingredient_priced_at(cur, "beurre", "0.00742", days_ago=42)
        add_price(cur, butter, "0.00876", days_ago=3)
        alert = recipe(cur, "croissant", portions=1, price="11.00")  # +13,4 pts
        component(cur, alert, 1000, ingredient=butter)
        watch = recipe(cur, "brioche", portions=1, price="110.00")  # +1,3 pt
        component(cur, watch, 1000, ingredient=butter)

        cur.execute(
            "SELECT recipe_id, level FROM report_margins WHERE recipe_id IN (%s, %s)",
            (alert, watch),
        )
        assert dict(cur.fetchall()) == {alert: "alerte", watch: "vigilance"}
        cur.execute("SELECT dishes_on_alert FROM report_summary")
        assert cur.fetchone()[0] == before + 1
