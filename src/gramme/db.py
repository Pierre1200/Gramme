"""Lecture du rapport en base.

Uniquement des SELECT sur les vues `rapport_*` (db/migrations/002_rapport.sql).
Aucun calcul ici : montants, ratios et écarts arrivent déjà calculés par
Postgres (décision 2). Ce module ne fait que les transporter jusqu'à la page.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import class_row


@dataclass(frozen=True)
class Resume:
    """Les trois tuiles de l'en-tête."""

    plats_en_alerte: int
    prix_en_hausse: int
    points_a_verifier: int


@dataclass(frozen=True)
class MargePlat:
    recette_id: int
    recette: str
    portions: int | None
    prix_vente_ttc: Decimal
    prix_vente_ht: Decimal
    cout_portion: Decimal
    marge_portion: Decimal
    marge_portion_avant: Decimal
    ratio: Decimal
    ratio_avant: Decimal
    ecart_points: Decimal
    # 'ok', 'vigilance' ou 'alerte' — décidé en SQL, la page ne compare rien.
    niveau: str


@dataclass(frozen=True)
class Variation:
    ingredient_id: int
    ingredient: str
    prix_avant: Decimal
    prix_apres: Decimal
    unite: str  # 'kg', 'L' ou 'pièce'
    variation_pct: Decimal


@dataclass(frozen=True)
class PointAttention:
    motif: str  # 'libelle_a_confirmer' ou 'total_incoherent'
    libelle: str
    fournisseur: str
    date_facture: date | None
    ecart: Decimal | None


@dataclass(frozen=True)
class Rapport:
    resume: Resume
    marges: list[MargePlat]
    variations: list[Variation]
    attention: list[PointAttention]


# Colonnes nommées explicitement plutôt que SELECT * : si une vue gagne une
# colonne, la page continue de fonctionner au lieu de casser sur un argument
# inattendu du constructeur. Les ORDER BY sont répétés ici : l'ordre d'une vue
# n'est pas garanti une fois qu'on la relit depuis l'extérieur.
_SQL_RESUME = """
    SELECT plats_en_alerte, prix_en_hausse, points_a_verifier
    FROM rapport_resume
"""
_SQL_MARGES = """
    SELECT recette_id, recette, portions, prix_vente_ttc, prix_vente_ht,
           cout_portion, marge_portion, marge_portion_avant,
           ratio, ratio_avant, ecart_points, niveau
    FROM rapport_marges
    ORDER BY ecart_points DESC, recette
"""
_SQL_VARIATIONS = """
    SELECT ingredient_id, ingredient, prix_avant, prix_apres, unite, variation_pct
    FROM rapport_variations
    ORDER BY variation_pct DESC, ingredient
"""
_SQL_ATTENTION = """
    SELECT motif, libelle, fournisseur, date_facture, ecart
    FROM rapport_attention
    ORDER BY motif, libelle
"""


def lire_rapport(url: str) -> Rapport:
    """Lit tout le rapport sur une connexion, dans une seule transaction.

    En REPEATABLE READ, les quatre requêtes voient le même instantané de la
    base. Sans ça, un prix inséré entre deux requêtes pourrait afficher
    « 2 plats à surveiller » au-dessus d'une liste qui en montre trois.

    `url` est passé explicitement : la démo lit `gramme_demo`, le vrai rapport
    lira la base principale, et ce module n'a pas à savoir laquelle.
    """
    with psycopg.connect(url) as connexion:
        connexion.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ

        with connexion.cursor(row_factory=class_row(Resume)) as cur:
            resume = cur.execute(_SQL_RESUME).fetchone()
        if resume is None:  # la vue renvoie toujours une ligne ; garde-fou
            raise RuntimeError("rapport_resume n'a renvoyé aucune ligne")

        with connexion.cursor(row_factory=class_row(MargePlat)) as cur:
            marges = cur.execute(_SQL_MARGES).fetchall()
        with connexion.cursor(row_factory=class_row(Variation)) as cur:
            variations = cur.execute(_SQL_VARIATIONS).fetchall()
        with connexion.cursor(row_factory=class_row(PointAttention)) as cur:
            attention = cur.execute(_SQL_ATTENTION).fetchall()

    return Rapport(resume=resume, marges=marges, variations=variations, attention=attention)
