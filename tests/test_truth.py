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

from gramme.truth import Regime, SourceQuality, TruthInvoice, TruthLine, load_truth

# Un ticket Carrefour minimal : montants en TTC, pas de numéro de facture.
ANNOTATION_TICKET = {
    "source_file": "2026-08-19_carrefour.jpg",
    "quality": "photo",
    "regime": "ttc",
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
    def test_ligne_complete(self):
        ligne = TruthLine(raw_label="BEURRE DOUX PLAQUE 25KG", qty=Decimal("2"))
        assert ligne.unreadable == []

    def test_libelle_obligatoire(self):
        # Pas de libellé lisible = pas de ligne, pas une ligne douteuse.
        with pytest.raises(ValidationError):
            TruthLine(raw_label="")

    def test_montants_tous_optionnels(self):
        # Une ligne peut être entièrement illisible sauf son libellé.
        ligne = TruthLine(
            raw_label="ARTICLE ILLISIBLE", unreadable=["qty", "unit_price_ht", "line_total_ht"]
        )
        assert ligne.qty is None

    def test_illisible_ne_peut_pas_porter_de_valeur(self):
        with pytest.raises(ValidationError, match="déclaré illisible"):
            TruthLine(raw_label="X", unit_price_ht=Decimal("8.47"), unreadable=["unit_price_ht"])

    def test_illisible_doit_nommer_un_champ_existant(self):
        # Attrape la faute de frappe d'annotation : "unit_price" au lieu de
        # "unit_price_ht" passerait sinon inaperçue et fausserait le score.
        with pytest.raises(ValidationError, match="champ inexistant"):
            TruthLine(raw_label="X", unreadable=["unit_price"])

    def test_champ_inattendu_refuse(self):
        with pytest.raises(ValidationError):
            TruthLine(raw_label="X", prix="8.47")


class TestTruthInvoice:
    def test_ticket_de_caisse(self):
        facture = TruthInvoice.model_validate(ANNOTATION_TICKET)
        assert facture.regime is Regime.TTC
        assert facture.quality is SourceQuality.PHOTO
        assert facture.invoice_number is None  # un ticket n'en porte pas
        assert len(facture.lines) == 2

    def test_regime_ht_par_defaut(self):
        # Champ absent de l'annotation : on retombe sur le cas courant, la
        # facture fournisseur en HT.
        sans_regime = {k: v for k, v in ANNOTATION_TICKET.items() if k != "regime"}
        assert TruthInvoice.model_validate(sans_regime).regime is Regime.HT

    def test_facture_sans_ligne_refusee(self):
        with pytest.raises(ValidationError):
            TruthInvoice.model_validate({**ANNOTATION_TICKET, "lines": []})

    def test_pieges_et_illisibles_en_entete(self):
        facture = TruthInvoice.model_validate(
            {
                **ANNOTATION_TICKET,
                "invoice_number": None,
                "total_ht": None,
                "unreadable": ["total_ht"],
                "pieges": ["remise de pied de page -2 %"],
            }
        )
        assert facture.unreadable == ["total_ht"]
        assert facture.pieges == ["remise de pied de page -2 %"]


class TestLoadTruth:
    def _ecrire(self, dossier: Path, nom: str, contenu: dict) -> None:
        (dossier / nom).write_text(json.dumps(contenu), encoding="utf-8")

    def test_charge_et_valide(self, tmp_path):
        self._ecrire(tmp_path, "a.json", ANNOTATION_TICKET)
        self._ecrire(tmp_path, "b.json", {**ANNOTATION_TICKET, "quality": "pdf"})
        assert len(load_truth(tmp_path, racine_raw=None)) == 2

    def test_dossier_vide_echoue(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="aucune annotation"):
            load_truth(tmp_path, racine_raw=None)

    def test_le_fichier_fautif_est_nomme(self, tmp_path):
        # Sans le nom du fichier, une erreur Pydantic sur quinze annotations
        # est inexploitable.
        self._ecrire(tmp_path, "cassee.json", {**ANNOTATION_TICKET, "quality": "floue"})
        with pytest.raises(ValueError, match="cassee.json"):
            load_truth(tmp_path, racine_raw=None)

    def test_document_source_manquant_echoue(self, tmp_path):
        truth, raw = tmp_path / "truth", tmp_path / "raw"
        truth.mkdir()
        raw.mkdir()
        self._ecrire(truth, "a.json", ANNOTATION_TICKET)
        with pytest.raises(FileNotFoundError, match="introuvable"):
            load_truth(truth, racine_raw=raw)

    def test_document_source_present(self, tmp_path):
        truth, raw = tmp_path / "truth", tmp_path / "raw"
        truth.mkdir()
        raw.mkdir()
        self._ecrire(truth, "a.json", ANNOTATION_TICKET)
        (raw / "2026-08-19_carrefour.jpg").write_bytes(b"")
        assert len(load_truth(truth, racine_raw=raw)) == 1
