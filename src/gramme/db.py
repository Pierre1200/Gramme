"""Lecture du rapport en base.

Uniquement des SELECT sur les vues `report_*` (db/migrations/002_rapport.sql).
Aucun calcul ici : montants, ratios et écarts arrivent déjà calculés par
Postgres (décision 2). Ce module ne fait que les transporter jusqu'à la page.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import class_row


class DatabaseUnreachable(RuntimeError):
    """La base ne répond pas. Distinguée d'une autre panne pour que les tests
    puissent s'ignorer sans masquer un vrai défaut.
    """


@dataclass(frozen=True)
class Summary:
    """Les trois tuiles de l'en-tête."""

    dishes_on_alert: int
    prices_up: int
    items_to_check: int


@dataclass(frozen=True)
class DishMargin:
    recipe_id: int
    recipe: str
    portions: int | None
    selling_price_incl_vat: Decimal
    selling_price: Decimal
    cost_per_portion: Decimal
    margin_per_portion: Decimal
    margin_per_portion_before: Decimal
    ratio: Decimal
    ratio_before: Decimal
    drift_points: Decimal
    # 'ok', 'vigilance' ou 'alerte' — décidé en SQL, la page ne compare rien.
    level: str


@dataclass(frozen=True)
class PriceChange:
    ingredient_id: int
    ingredient: str
    price_before: Decimal
    price_after: Decimal
    unit: str  # 'kg', 'L' ou 'pièce'
    change_pct: Decimal


@dataclass(frozen=True)
class Flag:
    reason: str  # 'label_to_confirm' ou 'total_mismatch'
    label: str
    supplier: str
    invoice_date: date | None
    gap: Decimal | None


@dataclass(frozen=True)
class Report:
    summary: Summary
    margins: list[DishMargin]
    price_changes: list[PriceChange]
    flags: list[Flag]


# Colonnes nommées explicitement plutôt que SELECT * : si une vue gagne une
# colonne, la page continue de fonctionner au lieu de casser sur un argument
# inattendu du constructeur. Les ORDER BY sont répétés ici : l'ordre d'une vue
# n'est pas garanti une fois qu'on la relit depuis l'extérieur.
_SUMMARY_SQL = """
    SELECT dishes_on_alert, prices_up, items_to_check
    FROM report_summary
"""
_MARGINS_SQL = """
    SELECT recipe_id, recipe, portions, selling_price_incl_vat, selling_price,
           cost_per_portion, margin_per_portion, margin_per_portion_before,
           ratio, ratio_before, drift_points, level
    FROM report_margins
    ORDER BY drift_points DESC, recipe
"""
_PRICE_CHANGES_SQL = """
    SELECT ingredient_id, ingredient, price_before, price_after, unit, change_pct
    FROM report_price_changes
    ORDER BY change_pct DESC, ingredient
"""
_FLAGS_SQL = """
    SELECT reason, label, supplier, invoice_date, gap
    FROM report_flags
    ORDER BY reason, label
"""


def read_report(url: str) -> Report:
    """Lit tout le rapport sur une connexion, dans une seule transaction.

    En REPEATABLE READ, les quatre requêtes voient le même instantané : sinon
    l'en-tête pourrait annoncer « 2 plats à surveiller » au-dessus d'une liste
    qui en montre trois. `url` est passé explicitement : la démo lit
    `gramme_demo`, le vrai rapport lira la base principale.
    """
    try:
        # Sans délai, une base éteinte fige la page au lieu d'afficher l'erreur.
        connection = psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError as e:
        raise DatabaseUnreachable(
            "base injoignable : lancer `make up`, puis `make demo-db` pour la démo"
        ) from e

    with connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ

        with connection.cursor(row_factory=class_row(Summary)) as cur:
            summary = cur.execute(_SUMMARY_SQL).fetchone()
        if summary is None:  # la vue renvoie toujours une ligne ; garde-fou
            raise RuntimeError("report_summary n'a renvoyé aucune ligne")

        with connection.cursor(row_factory=class_row(DishMargin)) as cur:
            margins = cur.execute(_MARGINS_SQL).fetchall()
        with connection.cursor(row_factory=class_row(PriceChange)) as cur:
            price_changes = cur.execute(_PRICE_CHANGES_SQL).fetchall()
        with connection.cursor(row_factory=class_row(Flag)) as cur:
            flags = cur.execute(_FLAGS_SQL).fetchall()

    return Report(summary=summary, margins=margins, price_changes=price_changes, flags=flags)
