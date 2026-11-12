"""Accès à la base : écriture des factures lues, lecture du rapport.

Aucun calcul ici, dans un sens comme dans l'autre (décision 2). À l'écriture,
seules les valeurs lues sur le document sont stockées ; à la lecture, montants,
ratios et écarts arrivent déjà calculés par Postgres, via les vues `report_*`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import psycopg
from psycopg.rows import class_row

from gramme.models import RawInvoice


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
    # None : coût inconnu, un ingrédient au moins n'a pas de prix (voir
    # missing_prices). Un coût incomplet n'est jamais affiché comme un coût.
    cost_per_portion: Decimal | None
    margin_per_portion: Decimal | None
    margin_per_portion_before: Decimal | None
    ratio: Decimal | None
    ratio_before: Decimal | None
    # None aussi quand seul le coût d'il y a 6 semaines est inconnu : un
    # ingrédient nouveau ne fabrique pas de dérive.
    drift_points: Decimal | None
    # 'ok', 'vigilance', 'alerte', ou None si la dérive est inconnue — décidé
    # en SQL, la page ne compare rien.
    level: str | None
    # Noms des ingrédients sans prix aujourd'hui ; vide si le coût est complet.
    missing_prices: list[str]


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
    reason: str  # 'label_to_confirm', 'total_mismatch' ou 'missing_price'
    label: str
    supplier: str | None  # None pour un prix manquant : aucun fournisseur en cause
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
           ratio, ratio_before, drift_points, level, missing_prices
    FROM report_margins
    ORDER BY drift_points DESC NULLS LAST, recipe
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


# ------------------------------------------------------------------ écriture


class StoreOutcome(StrEnum):
    """Ce qu'est devenue une facture présentée à la base."""

    STORED = "stored"
    ALREADY_STORED = "already_stored"  # même fichier, déjà reçu
    DUPLICATE_NUMBER = "duplicate_number"  # même numéro chez ce fournisseur, autre fichier
    TO_REVIEW = "to_review"  # un champ indispensable n'a pas été lu


def missing_fields(invoice: RawInvoice) -> list[str]:
    """Les champs sans lesquels une facture ne peut pas entrer en base.

    Une valeur non lue n'est jamais remplacée par une valeur supposée : la
    facture part en validation humaine, entière.
    """
    missing = [
        name for name in ("supplier_name", "invoice_date") if getattr(invoice, name).value is None
    ]
    for number, line in enumerate(invoice.lines, start=1):
        missing += [
            f"ligne {number} : {name}"
            for name in ("qty", "unit_price_ht")
            if getattr(line, name).value is None
        ]
    missing += [f"pied : {a.label}" for a in invoice.adjustments if a.amount_ht.value is None]
    return missing


def _supplier_id(cur: psycopg.Cursor, invoice: RawInvoice) -> int:
    """Le fournisseur, reconnu par son SIREN, à défaut par son nom exact."""
    name = invoice.supplier_name.value
    if invoice.supplier_siren:
        row = cur.execute(
            "SELECT id FROM supplier WHERE siren = %s", (invoice.supplier_siren,)
        ).fetchone()
    else:
        row = cur.execute("SELECT id FROM supplier WHERE name = %s", (name,)).fetchone()
    if row:
        return row[0]
    return cur.execute(
        "INSERT INTO supplier (name, siren) VALUES (%s, %s) RETURNING id",
        (name, invoice.supplier_siren),
    ).fetchone()[0]


def _product_id(cur: psycopg.Cursor, supplier_id: int, ref: str | None, label: str) -> int:
    """Le produit fournisseur : par sa référence, à défaut par son libellé.

    Créé en attente de rapprochement (`pending`), sans ingrédient : relier un
    libellé à un ingrédient est le travail de la normalisation.
    """
    if ref is not None:
        row = cur.execute(
            "SELECT id FROM supplier_product WHERE supplier_id = %s AND supplier_ref = %s",
            (supplier_id, ref),
        ).fetchone()
    else:
        row = cur.execute(
            "SELECT id FROM supplier_product"
            " WHERE supplier_id = %s AND supplier_ref IS NULL AND raw_label = %s",
            (supplier_id, label),
        ).fetchone()
    if row:
        return row[0]
    return cur.execute(
        "INSERT INTO supplier_product (supplier_id, supplier_ref, raw_label)"
        " VALUES (%s, %s, %s) RETURNING id",
        (supplier_id, ref, label),
    ).fetchone()[0]


def store_invoice(
    conn: psycopg.Connection,
    invoice: RawInvoice,
    *,
    source_uri: str,
    source_hash: str,
    extracted_by: str,
) -> StoreOutcome:
    """Écrit une facture lue, tout ou rien, et dit ce qu'elle est devenue.

    `source_hash` est l'empreinte SHA-256 du fichier (`intake.fingerprint`) :
    elle reconnaît un fichier renvoyé. `extracted_by` nomme ce qui a lu le
    document (« factur-x », ou le modèle pour une facture papier).

    Aucun prix relevé (`price_point`) n'est créé : un prix au gramme exige le
    contenu d'un colis, que seule la normalisation connaît.
    """
    if missing_fields(invoice):
        return StoreOutcome.TO_REVIEW

    with conn.transaction(), conn.cursor() as cur:
        if cur.execute("SELECT 1 FROM invoice WHERE source_hash = %s", (source_hash,)).fetchone():
            return StoreOutcome.ALREADY_STORED

        supplier_id = _supplier_id(cur, invoice)
        number = invoice.invoice_number.value
        if (
            number is not None
            and cur.execute(
                "SELECT 1 FROM invoice WHERE supplier_id = %s AND invoice_number = %s",
                (supplier_id, number),
            ).fetchone()
        ):
            return StoreOutcome.DUPLICATE_NUMBER

        invoice_id = cur.execute(
            """
            INSERT INTO invoice (supplier_id, invoice_number, invoice_date, total_ht,
                                 total_ttc, source_uri, source_hash, extracted_at,
                                 extraction_model)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
            RETURNING id
            """,
            (
                supplier_id,
                number,
                invoice.invoice_date.value,
                invoice.total_ht.value,
                invoice.total_ttc.value,
                source_uri,
                source_hash,
                extracted_by,
            ),
        ).fetchone()[0]

        for position, line in enumerate(invoice.lines, start=1):
            cur.execute(
                """
                INSERT INTO invoice_line (invoice_id, line_no, raw_label, supplier_product_id,
                                          qty, unit_price_ht, line_total_ht, raw_unit, vat_rate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    invoice_id,
                    line.line_no or position,
                    line.raw_label,
                    _product_id(cur, supplier_id, line.supplier_ref, line.raw_label),
                    line.qty.value,
                    line.unit_price_ht.value,
                    line.line_total_ht.value,
                    line.raw_unit.value,
                    line.vat_rate.value if line.vat_rate else None,
                ),
            )

        for adjustment in invoice.adjustments:
            cur.execute(
                "INSERT INTO invoice_adjustment (invoice_id, label, amount_ht) VALUES (%s, %s, %s)",
                (invoice_id, adjustment.label, adjustment.amount_ht.value),
            )

    return StoreOutcome.STORED
