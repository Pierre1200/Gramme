"""Le jeu de vérité doit refuser une annotation incohérente.

Une annotation fausse est pire qu'une annotation absente : elle produit une
mesure, et une mesure fausse se croit. Ces tests protègent la seule chose que
le banc d'essai tient pour acquise.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from gramme.truth import SourceQuality, TruthInvoice, TruthLine, VatBasis, load_truth

# Un ticket Carrefour minimal : montants en TTC, pas de numéro de facture.
TICKET = {
    "source_file": "2026-08-19_carrefour.jpg",
    "quality": "photo",
    "vat_basis": "ttc",
    "supplier_name": "CARREFOUR MARKET",
    "invoice_date": "2026-08-19",
    "total_ht": "47.83",
    "lines": [
        {
            "raw_label": "BEURRE DOUX 250G",
            "qty": "2",
            "raw_unit": "PCE",
            "unit_price_ht": "2.45",
            "line_total_ht": "4.90",
        },
        {
            "raw_label": "TOMATE GRAPPE",
            "qty": "0.842",
            "raw_unit": "KG",
            "unit_price_ht": "3.90",
            "line_total_ht": "3.28",
        },
    ],
}


class TestTruthLine:
    def test_a_complete_line(self):
        line = TruthLine(raw_label="BEURRE DOUX PLAQUE 25KG", qty=Decimal("2"))
        assert line.unreadable == []

    def test_the_label_is_required(self):
        # Pas de libellé lisible = pas de ligne, pas une ligne douteuse.
        with pytest.raises(ValidationError):
            TruthLine(raw_label="")

    def test_every_amount_is_optional(self):
        # Une ligne peut être entièrement illisible sauf son libellé.
        line = TruthLine(
            raw_label="ARTICLE ILLISIBLE", unreadable=["qty", "unit_price_ht", "line_total_ht"]
        )
        assert line.qty is None

    def test_an_unreadable_field_cannot_hold_a_value(self):
        with pytest.raises(ValidationError, match="déclaré illisible"):
            TruthLine(raw_label="X", unit_price_ht=Decimal("8.47"), unreadable=["unit_price_ht"])

    def test_unreadable_must_name_an_existing_field(self):
        # Attrape la faute de frappe d'annotation : "unit_price" au lieu de
        # "unit_price_ht" passerait sinon inaperçue et fausserait le score.
        with pytest.raises(ValidationError, match="champ inexistant"):
            TruthLine(raw_label="X", unreadable=["unit_price"])

    def test_an_unexpected_field_is_refused(self):
        with pytest.raises(ValidationError):
            TruthLine(raw_label="X", price="8.47")


class TestTruthInvoice:
    def test_a_till_receipt(self):
        invoice = TruthInvoice.model_validate(TICKET)
        assert invoice.vat_basis is VatBasis.INCLUDING
        assert invoice.quality is SourceQuality.PHOTO
        assert invoice.invoice_number is None  # un ticket n'en porte pas
        assert len(invoice.lines) == 2

    def test_amounts_are_excluding_vat_by_default(self):
        # Champ absent de l'annotation : on retombe sur le cas courant, la
        # facture fournisseur en HT.
        without_basis = {k: v for k, v in TICKET.items() if k != "vat_basis"}
        assert TruthInvoice.model_validate(without_basis).vat_basis is VatBasis.EXCLUDING

    def test_an_invoice_without_lines_is_refused(self):
        with pytest.raises(ValidationError):
            TruthInvoice.model_validate({**TICKET, "lines": []})

    def test_pitfalls_and_unreadable_header_fields(self):
        invoice = TruthInvoice.model_validate(
            {
                **TICKET,
                "invoice_number": None,
                "total_ht": None,
                "unreadable": ["total_ht"],
                "pitfalls": ["remise de pied de page -2 %"],
            }
        )
        assert invoice.unreadable == ["total_ht"]
        assert invoice.pitfalls == ["remise de pied de page -2 %"]


class TestLoadTruth:
    def _write(self, directory: Path, name: str, content: dict) -> None:
        (directory / name).write_text(json.dumps(content), encoding="utf-8")

    def test_it_loads_and_validates(self, tmp_path):
        self._write(tmp_path, "a.json", TICKET)
        self._write(tmp_path, "b.json", {**TICKET, "quality": "pdf"})
        assert len(load_truth(tmp_path, raw_root=None)) == 2

    def test_an_empty_directory_fails(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="aucune annotation"):
            load_truth(tmp_path, raw_root=None)

    def test_the_faulty_file_is_named(self, tmp_path):
        # Sans le nom du fichier, une erreur Pydantic sur quinze annotations
        # est inexploitable.
        self._write(tmp_path, "cassee.json", {**TICKET, "quality": "floue"})
        with pytest.raises(ValueError, match="cassee.json"):
            load_truth(tmp_path, raw_root=None)

    def test_a_missing_source_document_fails(self, tmp_path):
        truth, raw = tmp_path / "truth", tmp_path / "raw"
        truth.mkdir()
        raw.mkdir()
        self._write(truth, "a.json", TICKET)
        with pytest.raises(FileNotFoundError, match="introuvable"):
            load_truth(truth, raw_root=raw)

    def test_a_present_source_document(self, tmp_path):
        truth, raw = tmp_path / "truth", tmp_path / "raw"
        truth.mkdir()
        raw.mkdir()
        self._write(truth, "a.json", TICKET)
        (raw / "2026-08-19_carrefour.jpg").write_bytes(b"")
        assert len(load_truth(truth, raw_root=raw)) == 1
