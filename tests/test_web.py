"""Les deux pages de la démo, sans base ni dossier réel.

Le rapport est remplacé par une doublure et le dossier de rangement par un
dossier temporaire : ces tests vérifient la page, pas le calcul (qui est testé
dans Postgres par test_cost.py).
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from gramme import web
from gramme.db import MargePlat, PointAttention, Rapport, Resume, Variation

RAPPORT = Rapport(
    resume=Resume(plats_en_alerte=1, prix_en_hausse=1, points_a_verifier=1),
    marges=[
        MargePlat(
            recette_id=4,
            recette="Paella mixte, la part",
            portions=50,
            prix_vente_ttc=Decimal("12.00"),
            prix_vente_ht=Decimal("10.91"),
            cout_portion=Decimal("3.58"),
            marge_portion=Decimal("7.33"),
            marge_portion_avant=Decimal("7.63"),
            ratio=Decimal("32.8"),
            ratio_avant=Decimal("30.0"),
            ecart_points=Decimal("2.8"),
            niveau="alerte",
        )
    ],
    variations=[
        Variation(5, "Gambas entières", Decimal("14.00"), Decimal("16.80"), "kg", Decimal("20.0")),
        Variation(24, "Tomates", Decimal("2.20"), Decimal("2.02"), "kg", Decimal("-8.2")),
    ],
    attention=[
        PointAttention(
            "total_incoherent",
            "MP-0418",
            "Marée du port (exemple)",
            date(2026, 9, 12),
            Decimal("4.20"),
        )
    ],
)


@pytest.fixture
def raw(tmp_path):
    return tmp_path / "raw"


@pytest.fixture
def client(raw):
    web.app.dependency_overrides[web.rapport_de_demo] = lambda: RAPPORT
    web.app.dependency_overrides[web.dossier_raw] = lambda: raw
    yield TestClient(web.app)
    web.app.dependency_overrides.clear()


def envoyer(client, nom: str, contenu: bytes = b"\xff\xd8 photo de facture"):
    return client.post("/envoyer", files={"fichier": (nom, contenu, "image/jpeg")})


class TestRapport:
    def test_racine_renvoie_vers_le_rapport(self, client):
        reponse = client.get("/", follow_redirects=False)
        assert reponse.status_code == 307
        assert reponse.headers["location"] == "/rapport"

    def test_bandeau_chiffres_fictifs_toujours_visible(self, client):
        # Règle de la démo : rien d'illustratif n'est présenté comme réel.
        assert "chiffres fictifs" in client.get("/rapport").text

    def test_chiffres_mis_en_forme_a_la_francaise(self, client):
        page = client.get("/rapport").text
        assert "Paella mixte, la part" in page
        assert "7,33 €" in page
        assert "+2,8 pts" in page
        assert "−8,2 %" in page  # vrai signe moins
        assert "4,20 €" in page

    def test_pas_de_documentation_api_exposee(self, client):
        assert client.get("/docs").status_code == 404


class TestEnvoi:
    def test_facture_recue_et_rangee(self, client, raw):
        reponse = envoyer(client, "image.jpg")
        assert reponse.status_code == 200
        assert "Facture reçue" in reponse.text
        assert [f.name for f in raw.iterdir()] == ["image.jpg"]

    def test_la_page_ne_pretend_jamais_avoir_lu_la_facture(self, client):
        page = envoyer(client, "image.jpg").text
        assert "la lecture automatique n'est pas encore branchée" in page

    def test_meme_fichier_deux_fois(self, client, raw):
        envoyer(client, "image.jpg")
        reponse = envoyer(client, "IMG_4821.jpg")  # même contenu, autre nom
        assert "Déjà reçue" in reponse.text
        assert len(list(raw.iterdir())) == 1

    def test_deux_photos_differentes_au_meme_nom(self, client, raw):
        # Safari envoie « image.jpg » pour chaque photo : il faut garder les deux.
        envoyer(client, "image.jpg", b"premiere")
        envoyer(client, "image.jpg", b"seconde")
        assert sorted(f.name for f in raw.iterdir()) == ["image.jpg", "image_2.jpg"]

    def test_format_refuse(self, client, raw):
        reponse = envoyer(client, "notes.txt")
        assert reponse.status_code == 400
        assert "Format non pris en charge" in reponse.text
        assert not raw.exists()

    def test_formulaire_sans_fichier(self, client):
        reponse = client.post("/envoyer", data={})
        assert reponse.status_code == 400
        assert "Aucun fichier reçu" in reponse.text

    def test_fichier_trop_lourd(self, client, raw, monkeypatch):
        monkeypatch.setattr(web, "TAILLE_MAX", 10)
        reponse = envoyer(client, "image.jpg", b"x" * 11)
        assert reponse.status_code == 413
        assert not raw.exists()

    def test_taille_annoncee_refusee_avant_de_lire_le_corps(self, client, raw, monkeypatch):
        # Le refus doit tomber sur l'en-tête content-length : sinon Starlette a
        # déjà écrit le fichier sur disque quand on le rejette.
        monkeypatch.setattr(web, "TAILLE_MAX", 10)
        reponse = client.post(
            "/envoyer",
            content=b"x" * 50,
            headers={"content-type": "application/octet-stream", "content-length": "50"},
        )
        assert reponse.status_code == 413
        assert not raw.exists()

    def test_nom_de_fichier_malveillant_reste_dans_le_dossier(self, client, raw, tmp_path):
        envoyer(client, "../../evasion.jpg")
        assert [f.name for f in raw.iterdir()] == ["evasion.jpg"]
        assert not (tmp_path / "evasion.jpg").exists()


class TestFiltres:
    def test_euros(self):
        assert web.euros(Decimal("1234.5")) == "1 234,50 €"
        assert web.euros(None) == "—"

    def test_points_pluriel_a_partir_de_deux(self):
        assert web.points(Decimal("1.6")) == "+1,6 pt"
        assert web.points(Decimal("2.0")) == "+2,0 pts"

    def test_variation_toujours_signee(self):
        assert web.variation(Decimal("20.0")) == "+20,0 %"
        assert web.variation(Decimal("-8.2")) == "−8,2 %"

    def test_date_longue(self):
        assert web.date_longue(date(2026, 9, 15)) == "mardi 15 septembre 2026"

    def test_jour_mois(self):
        assert web.jour_mois(date(2026, 9, 14)) == "14 septembre"


def test_le_rapport_annonce_la_semaine_et_non_le_jour(client):
    # La démo peut tomber un mardi : « Rapport du lundi — mardi 15 » se contredirait.
    assert "Semaine du" in client.get("/rapport").text
