"""Le contrat entre les vues SQL et les objets de db.py.

Si une vue renomme ou supprime une colonne, `class_row` échoue : ces tests le
voient avant la page. Ils lisent la base de démonstration (`make demo-db`) et
sont ignorés si elle n'existe pas.

On vérifie la cohérence entre sections, pas les chiffres du seed : ceux-là
peuvent être retouchés sans que le code soit faux.
"""

import psycopg
import pytest

from gramme.config import get_settings
from gramme.db import lire_rapport


@pytest.fixture(scope="module")
def rapport():
    try:
        return lire_rapport(get_settings().demo_database_url)
    except psycopg.OperationalError:
        pytest.skip("gramme_demo injoignable : lancer `make up` puis `make demo-db`")


def test_toutes_les_sections_sont_alimentees(rapport):
    assert rapport.marges
    assert rapport.variations
    assert rapport.attention


def test_la_tuile_correspond_a_la_liste(rapport):
    # Lu dans un seul instantané : l'en-tête et les listes décrivent le même état.
    assert rapport.resume.plats_en_alerte == len(
        [m for m in rapport.marges if m.niveau == "alerte"]
    )
    assert rapport.resume.prix_en_hausse == len(
        [v for v in rapport.variations if v.variation_pct > 0]
    )
    assert rapport.resume.points_a_verifier == len(rapport.attention)


def test_niveaux_connus(rapport):
    # Le template associe une couleur à chaque niveau : un niveau inconnu
    # s'afficherait sans style.
    assert {m.niveau for m in rapport.marges} <= {"ok", "vigilance", "alerte"}


def test_plats_tries_du_plus_derivant_au_plus_stable(rapport):
    ecarts = [m.ecart_points for m in rapport.marges]
    assert ecarts == sorted(ecarts, reverse=True)
