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
from gramme.db import DishMargin, Flag, PriceChange, Report, Summary

REPORT = Report(
    summary=Summary(dishes_on_alert=1, prices_up=1, items_to_check=1),
    margins=[
        DishMargin(
            recipe_id=4,
            recipe="Paella mixte, la part",
            portions=50,
            selling_price_incl_vat=Decimal("12.00"),
            selling_price=Decimal("10.91"),
            cost_per_portion=Decimal("3.58"),
            margin_per_portion=Decimal("7.33"),
            margin_per_portion_before=Decimal("7.63"),
            ratio=Decimal("32.8"),
            ratio_before=Decimal("30.0"),
            drift_points=Decimal("2.8"),
            level="alerte",
        )
    ],
    price_changes=[
        PriceChange(
            5, "Gambas entières", Decimal("14.00"), Decimal("16.80"), "kg", Decimal("20.0")
        ),
        PriceChange(24, "Tomates", Decimal("2.20"), Decimal("2.02"), "kg", Decimal("-8.2")),
    ],
    flags=[
        Flag(
            "total_mismatch",
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
    web.app.dependency_overrides[web.demo_report] = lambda: REPORT
    web.app.dependency_overrides[web.raw_dir] = lambda: raw
    yield TestClient(web.app)
    web.app.dependency_overrides.clear()


def upload(client, name: str, content: bytes = b"\xff\xd8 photo de facture"):
    return client.post("/envoyer", files={"fichier": (name, content, "image/jpeg")})


class TestReportPage:
    def test_the_root_redirects_to_the_report(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/rapport"

    def test_the_fictional_figures_banner_is_always_visible(self, client):
        # Règle de la démo : rien d'illustratif n'est présenté comme réel.
        assert "chiffres fictifs" in client.get("/rapport").text

    def test_figures_are_formatted_the_french_way(self, client):
        page = client.get("/rapport").text
        assert "Paella mixte, la part" in page
        assert "7,33 €" in page
        assert "+2,8 pts" in page
        assert "−8,2 %" in page  # vrai signe moins
        assert "4,20 €" in page

    def test_the_week_is_announced_not_the_day(self, client):
        # La démo peut tomber un mardi : « Rapport du lundi — mardi 15 » se
        # contredirait.
        assert "Semaine du" in client.get("/rapport").text

    def test_no_api_documentation_is_exposed(self, client):
        assert client.get("/docs").status_code == 404


class TestUpload:
    def test_an_invoice_is_received_and_stored(self, client, raw):
        response = upload(client, "image.jpg")
        assert response.status_code == 200
        assert "Facture reçue" in response.text
        assert [f.name for f in raw.iterdir()] == ["image.jpg"]

    def test_the_page_never_claims_to_have_read_the_invoice(self, client):
        page = upload(client, "image.jpg").text
        assert "la lecture automatique n'est pas encore branchée" in page

    def test_the_same_file_sent_twice(self, client, raw):
        upload(client, "image.jpg")
        response = upload(client, "IMG_4821.jpg")  # même contenu, autre nom
        assert "Déjà reçue" in response.text
        assert len(list(raw.iterdir())) == 1

    def test_two_different_photos_sharing_a_name(self, client, raw):
        # Safari envoie « image.jpg » pour chaque photo : il faut garder les deux.
        upload(client, "image.jpg", b"premiere")
        upload(client, "image.jpg", b"seconde")
        assert sorted(f.name for f in raw.iterdir()) == ["image.jpg", "image_2.jpg"]

    def test_an_unsupported_format_is_refused(self, client, raw):
        response = upload(client, "notes.txt")
        assert response.status_code == 400
        assert "Format non pris en charge" in response.text
        assert not raw.exists()

    def test_a_form_without_a_file(self, client):
        response = client.post("/envoyer", data={})
        assert response.status_code == 400
        assert "Aucun fichier reçu" in response.text

    def test_a_file_that_is_too_large(self, client, raw, monkeypatch):
        monkeypatch.setattr(web, "MAX_SIZE", 10)
        response = upload(client, "image.jpg", b"x" * 11)
        assert response.status_code == 413
        assert not raw.exists()

    def test_the_announced_size_is_refused_before_reading_the_body(self, client, raw, monkeypatch):
        # Le refus doit tomber sur l'en-tête content-length : sinon Starlette a
        # déjà écrit le fichier sur disque quand on le rejette.
        monkeypatch.setattr(web, "MAX_SIZE", 10)
        response = client.post(
            "/envoyer",
            content=b"x" * 50,
            headers={"content-type": "application/octet-stream", "content-length": "50"},
        )
        assert response.status_code == 413
        assert not raw.exists()

    def test_a_malicious_filename_stays_inside_the_directory(self, client, raw, tmp_path):
        upload(client, "../../evasion.jpg")
        assert [f.name for f in raw.iterdir()] == ["evasion.jpg"]
        assert not (tmp_path / "evasion.jpg").exists()


class TestFilters:
    def test_euros(self):
        assert web.euros(Decimal("1234.5")) == "1 234,50 €"
        assert web.euros(None) == "—"

    def test_drift_is_plural_from_two_points(self):
        assert web.drift(Decimal("1.6")) == "+1,6 pt"
        assert web.drift(Decimal("2.0")) == "+2,0 pts"

    def test_a_change_is_always_signed(self):
        assert web.change(Decimal("20.0")) == "+20,0 %"
        assert web.change(Decimal("-8.2")) == "−8,2 %"

    def test_long_date(self):
        assert web.long_date(date(2026, 9, 15)) == "mardi 15 septembre 2026"

    def test_day_month(self):
        assert web.day_month(date(2026, 9, 14)) == "14 septembre"
