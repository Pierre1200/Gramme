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
        connexion = psycopg.connect(get_settings().database_url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres injoignable : lancer `make up`")
    with connexion:
        with connexion.cursor() as curseur:
            yield curseur
        connexion.rollback()  # rien ne survit au test


def inserer(cur, sql: str, *params) -> int:
    cur.execute(sql + " RETURNING id", params)
    return cur.fetchone()[0]


def ingredient_au_prix(cur, nom: str, prix_par_gramme: str, il_y_a_jours: int = 0) -> int:
    """Crée un ingrédient, son produit fournisseur confirmé et un relevé de prix."""
    fournisseur = inserer(cur, "INSERT INTO supplier (name) VALUES ('test')")
    ingredient = inserer(cur, "INSERT INTO ingredient (name, base_unit) VALUES (%s, 'g')", nom)
    inserer(
        cur,
        "INSERT INTO supplier_product (supplier_id, raw_label, ingredient_id, status)"
        " VALUES (%s, %s, %s, 'confirmed')",
        fournisseur,
        nom.upper(),
        ingredient,
    )
    # L'identifiant de l'INGRÉDIENT, pas celui du produit fournisseur. Les deux
    # coïncident sur une base neuve puis divergent : les séquences Postgres ne
    # sont pas annulées par un ROLLBACK. Ce bug a fait passer des tests par hasard.
    ajouter_prix(cur, ingredient, prix_par_gramme, il_y_a_jours)
    return ingredient


def ajouter_prix(cur, ingredient: int, prix_par_gramme: str, il_y_a_jours: int) -> None:
    """Ajoute un relevé sur le produit fournisseur de cet ingrédient."""
    cur.execute(
        "INSERT INTO price_point (supplier_product_id, observed_on, price_per_base_unit)"
        " SELECT id, CURRENT_DATE - %s, %s FROM supplier_product WHERE ingredient_id = %s",
        (il_y_a_jours, Decimal(prix_par_gramme), ingredient),
    )


def recette(
    cur, nom: str, rendement: int = 1, portions: int | None = None, prix: str | None = None
) -> int:
    return inserer(
        cur,
        "INSERT INTO recipe (name, yield_qty, yield_unit, is_sellable, portions,"
        " selling_price_ttc, vat_rate) VALUES (%s, %s, 'g', %s, %s, %s, 0.100)",
        nom,
        rendement,
        prix is not None,
        portions,
        prix,
    )


def composant(cur, parent: int, qty: int, *, ingredient=None, enfant=None, rendement="1.0") -> None:
    cur.execute(
        "INSERT INTO recipe_component (parent_recipe_id, ingredient_id, child_recipe_id,"
        " qty, unit, yield_ratio) VALUES (%s, %s, %s, %s, 'g', %s)",
        (parent, ingredient, enfant, qty, Decimal(rendement)),
    )


def cout(cur, recette_id: int, il_y_a_jours: int | None = None) -> Decimal:
    if il_y_a_jours is None:
        cur.execute("SELECT recipe_cost(%s)", (recette_id,))
    else:
        cur.execute("SELECT recipe_cost_at(%s, CURRENT_DATE - %s)", (recette_id, il_y_a_jours))
    return cur.fetchone()[0].quantize(Decimal("0.01"))


class TestCoutDeRecette:
    def test_rendement_calcule_sur_le_brut(self, cur):
        # 2 250 g de gambas décortiquées à rendement 0,50 = 4 500 g achetés.
        gambas = ingredient_au_prix(cur, "gambas", "0.0168")
        paella = recette(cur, "paella")
        composant(cur, paella, 2250, ingredient=gambas, rendement="0.50")
        assert cout(cur, paella) == Decimal("75.60")

    def test_propagation_dans_une_sous_recette(self, cur):
        # La tarte utilise 400 g d'une pâte dont le batch fait 1 000 g : elle
        # porte 40 % du coût du batch.
        beurre = ingredient_au_prix(cur, "beurre", "0.008")
        pate = recette(cur, "pâte", rendement=1000)
        composant(cur, pate, 500, ingredient=beurre)  # batch : 4,00 €
        tarte = recette(cur, "tarte")
        composant(cur, tarte, 400, enfant=pate)
        assert cout(cur, tarte) == Decimal("1.60")

    def test_cout_du_jour_egal_cout_a_la_date_du_jour(self, cur):
        beurre = ingredient_au_prix(cur, "beurre", "0.008")
        plat = recette(cur, "plat")
        composant(cur, plat, 100, ingredient=beurre)
        cur.execute("SELECT recipe_cost(%s) = recipe_cost_at(%s, CURRENT_DATE)", (plat, plat))
        assert cur.fetchone()[0] is True


class TestCoutAUneDate:
    def test_lit_le_prix_en_vigueur_a_cette_date(self, cur):
        beurre = ingredient_au_prix(cur, "beurre", "0.00742", il_y_a_jours=42)
        ajouter_prix(cur, beurre, "0.00876", il_y_a_jours=3)
        plat = recette(cur, "croissants")
        composant(cur, plat, 1000, ingredient=beurre)
        assert cout(cur, plat, il_y_a_jours=42) == Decimal("7.42")
        assert cout(cur, plat) == Decimal("8.76")

    def test_ignore_les_prix_posterieurs(self, cur):
        beurre = ingredient_au_prix(cur, "beurre", "0.008", il_y_a_jours=10)
        plat = recette(cur, "plat")
        composant(cur, plat, 1000, ingredient=beurre)
        assert cout(cur, plat, il_y_a_jours=42) == Decimal("0.00")


class TestLimiteConnue:
    def test_ingredient_sans_prix_disparait_en_silence(self, cur):
        """Verrouille un défaut ACTUEL, documenté dans CLAUDE.md.

        La jointure interne fait disparaître un ingrédient sans prix : le coût
        est sous-estimé sans aucun avertissement. Le jour où c'est corrigé, ce
        test doit casser et être réécrit — c'est voulu.
        """
        beurre = ingredient_au_prix(cur, "beurre", "0.008")
        sans_prix = inserer(cur, "INSERT INTO ingredient (name, base_unit) VALUES ('safran', 'g')")
        plat = recette(cur, "plat")
        composant(cur, plat, 1000, ingredient=beurre)
        composant(cur, plat, 1, ingredient=sans_prix)
        assert cout(cur, plat) == Decimal("8.00")  # le safran n'est pas compté


class TestVuesDuRapport:
    def test_marge_brute_en_euros(self, cur):
        # 12 € TTC à 10 % = 10,91 € HT ; coût 3,60 € par part -> 7,31 €.
        riz = ingredient_au_prix(cur, "riz", "0.0036")
        paella = recette(cur, "paella", portions=1, prix="12.00")
        composant(cur, paella, 1000, ingredient=riz)
        cur.execute("SELECT marge_brute_ht FROM recipe_margin WHERE id = %s", (paella,))
        assert cur.fetchone()[0] == Decimal("7.31")

    def test_derive_du_ratio_sur_six_semaines(self, cur):
        beurre = ingredient_au_prix(cur, "beurre", "0.00742", il_y_a_jours=42)
        ajouter_prix(cur, beurre, "0.00876", il_y_a_jours=3)
        plat = recette(cur, "croissant", portions=1, prix="11.00")  # 10,00 € HT
        composant(cur, plat, 1000, ingredient=beurre)
        cur.execute(
            "SELECT ratio, ratio_avant, ecart_points, niveau FROM rapport_marges"
            " WHERE recette_id = %s",
            (plat,),
        )
        assert cur.fetchone() == (Decimal("87.6"), Decimal("74.2"), Decimal("13.4"), "alerte")

    def test_total_de_facture_incoherent_signale(self, cur):
        fournisseur = inserer(cur, "INSERT INTO supplier (name) VALUES ('marée (test)')")
        for numero, total in (("OK", "100.04"), ("KO", "104.20")):
            facture = inserer(
                cur,
                "INSERT INTO invoice (supplier_id, invoice_number, invoice_date, total_ht,"
                " source_uri, source_hash) VALUES (%s, %s, CURRENT_DATE, %s, 'test', %s)",
                fournisseur,
                numero,
                Decimal(total),
                f"hash-{numero}",
            )
            cur.execute(
                "INSERT INTO invoice_line (invoice_id, raw_label, qty, unit_price_ht,"
                " line_total_ht) VALUES (%s, 'BAR', 1, 100, 100)",
                (facture,),
            )
        cur.execute(
            "SELECT libelle, ecart FROM rapport_attention WHERE motif = 'total_incoherent'"
            " AND fournisseur = 'marée (test)'"
        )
        # 0,04 € d'écart reste sous la tolérance ; 4,20 € la dépasse.
        assert cur.fetchall() == [("KO", Decimal("4.20"))]

    def test_la_tuile_ne_compte_que_les_alertes(self, cur):
        """Décision du 15/09 : seules les alertes comptent en en-tête.

        La vigilance reste visible plat par plat. Mesuré en différence avant et
        après insertion, pour ne pas dépendre du contenu de la base.
        """
        cur.execute("SELECT plats_en_alerte FROM rapport_resume")
        avant = cur.fetchone()[0]

        beurre = ingredient_au_prix(cur, "beurre", "0.00742", il_y_a_jours=42)
        ajouter_prix(cur, beurre, "0.00876", il_y_a_jours=3)
        alerte = recette(cur, "croissant", portions=1, prix="11.00")  # +13,4 pts
        composant(cur, alerte, 1000, ingredient=beurre)
        vigilance = recette(cur, "brioche", portions=1, prix="110.00")  # +1,3 pt
        composant(cur, vigilance, 1000, ingredient=beurre)

        cur.execute(
            "SELECT recette_id, niveau FROM rapport_marges WHERE recette_id IN (%s, %s)",
            (alerte, vigilance),
        )
        assert dict(cur.fetchall()) == {alerte: "alerte", vigilance: "vigilance"}
        cur.execute("SELECT plats_en_alerte FROM rapport_resume")
        assert cur.fetchone()[0] == avant + 1
