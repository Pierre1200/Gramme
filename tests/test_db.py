"""Le contrat entre les vues SQL et les objets de db.py.

Si une vue renomme ou supprime une colonne, `class_row` échoue : ces tests le
voient avant la page. Ils lisent la base de démonstration (`make demo-db`) et
sont ignorés si elle n'existe pas.

On vérifie la cohérence entre sections, pas les chiffres du seed : ceux-là
peuvent être retouchés sans que le code soit faux.
"""

import pytest

from gramme.config import get_settings
from gramme.db import DatabaseUnreachable, read_report


@pytest.fixture(scope="module")
def report():
    try:
        return read_report(get_settings().demo_database_url)
    except DatabaseUnreachable:
        pytest.skip("gramme_demo injoignable : lancer `make up` puis `make demo-db`")


def test_every_section_is_filled(report):
    assert report.margins
    assert report.price_changes
    assert report.flags


def test_the_tile_matches_the_list(report):
    # Lu dans un seul instantané : l'en-tête et les listes décrivent le même état.
    assert report.summary.dishes_on_alert == len([m for m in report.margins if m.level == "alerte"])
    assert report.summary.prices_up == len([c for c in report.price_changes if c.change_pct > 0])
    assert report.summary.items_to_check == len(report.flags)


def test_known_levels_only(report):
    # Le template associe une couleur à chaque niveau : un niveau inconnu
    # s'afficherait sans style.
    # None : dérive inconnue, carte « Pas de recul » ou « Coût incomplet ».
    assert {m.level for m in report.margins} <= {"ok", "vigilance", "alerte", None}


def test_dishes_are_sorted_from_most_drifting_to_most_stable(report):
    # Les dérives inconnues ferment la liste : les alertes d'abord.
    drifts = [m.drift_points for m in report.margins]
    known = [d for d in drifts if d is not None]
    assert drifts == sorted(known, reverse=True) + [None] * (len(drifts) - len(known))


def test_the_demo_has_no_incomplete_dish(report):
    # La démo montre des cartes complètes : un prix manquant dans le seed
    # afficherait « Coût incomplet » devant l'établissement.
    assert all(not m.missing_prices for m in report.margins)
    assert all(f.reason != "missing_price" for f in report.flags)
