"""Les invariants des contrats d'extraction.

Ce qui est testé ici, ce n'est pas Pydantic : c'est la promesse qu'un champ
ne peut pas porter une valeur sans l'assumer, et qu'aucun montant n'est
reconstruit à partir des autres.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gramme.models import (
    ArithmeticCheck,
    Extracted,
    FieldStatus,
    RawInvoice,
    RawInvoiceLine,
)


def lu[T](type_: type[T], valeur: T, confiance: float = 0.95) -> Extracted[T]:
    return Extracted[type_](
        status=FieldStatus.READ, value=valeur, raw_text=str(valeur), confidence=confiance
    )


def ligne(qty: str, prix: str, total: str) -> RawInvoiceLine:
    return RawInvoiceLine(
        raw_label="BEURRE DOUX PLAQUE 25KG",
        qty=lu(Decimal, Decimal(qty)),
        raw_unit=lu(str, "KG"),
        unit_price_ht=lu(Decimal, Decimal(prix)),
        line_total_ht=lu(Decimal, Decimal(total)),
    )


def facture(total_ht: str, lignes: list[RawInvoiceLine]) -> RawInvoice:
    return RawInvoice(
        supplier_name=lu(str, "METRO"),
        invoice_number=lu(str, "FA-2026-1187"),
        invoice_date=lu(date, date(2026, 8, 12)),
        total_ht=lu(Decimal, Decimal(total_ht)),
        total_ttc=lu(Decimal, Decimal(total_ht) * Decimal("1.055")),
        lines=lignes,
    )


class TestExtracted:
    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"status": "read", "raw_text": "?"}, id="read sans valeur"),
            pytest.param({"status": "read", "value": Decimal(1)}, id="read sans raw_text"),
            pytest.param(
                {"status": "unreadable", "value": Decimal(1)}, id="unreadable avec valeur"
            ),
            pytest.param(
                {"status": "unreadable", "confidence": 0.8}, id="unreadable avec confiance"
            ),
            pytest.param({"status": "absent", "note": "x"}, id="champ inattendu"),
        ],
    )
    def test_etats_incoherents_refuses(self, kwargs):
        with pytest.raises(ValidationError):
            Extracted[Decimal](**kwargs)

    def test_absent_ne_demande_pas_de_relecture(self):
        # Un champ que le document ne porte pas n'est pas une erreur : il ne
        # doit pas polluer la file de validation.
        assert Extracted[Decimal](status=FieldStatus.ABSENT).needs_review(0.9) is False

    def test_unreadable_demande_toujours_une_relecture(self):
        assert Extracted[Decimal](status=FieldStatus.UNREADABLE).needs_review(0.0) is True

    def test_confiance_sous_le_seuil(self):
        assert lu(Decimal, Decimal(1), confiance=0.7).needs_review(0.9) is True
        assert lu(Decimal, Decimal(1), confiance=0.95).needs_review(0.9) is False

    def test_immuable(self):
        with pytest.raises(ValidationError):
            lu(Decimal, Decimal(1)).confidence = 0.1


class TestRecoupementLigne:
    def test_arrondi_fournisseur_accepte(self):
        # 12,5 × 8,47 = 105,875, imprimé 105,88.
        assert ligne("12.5", "8.47", "105.88").check_arithmetic() is ArithmeticCheck.OK

    def test_incoherence_detectee(self):
        assert ligne("12.5", "8.47", "158.88").check_arithmetic() is ArithmeticCheck.MISMATCH

    def test_champ_illisible_ne_reconstruit_rien(self):
        ligne_ko = RawInvoiceLine(
            raw_label="X",
            qty=lu(Decimal, Decimal("12.5")),
            raw_unit=lu(str, "KG"),
            unit_price_ht=Extracted[Decimal](status=FieldStatus.UNREADABLE, raw_text="8,4?"),
            line_total_ht=lu(Decimal, Decimal("105.88")),
        )
        assert ligne_ko.check_arithmetic() is ArithmeticCheck.INCOMPLETE
        assert ligne_ko.unit_price_ht.value is None  # surtout pas 8.47 déduit du total


class TestRecoupementFacture:
    def test_somme_coherente(self):
        f = facture("219.00", [ligne("1", "100.00", "100.00"), ligne("1", "119.00", "119.00")])
        assert f.check_total() is ArithmeticCheck.OK

    def test_ligne_manquante_detectee(self):
        # Le cas qu'aucune enveloppe par champ ne peut voir : bas de page coupé.
        f = facture("219.00", [ligne("1", "100.00", "100.00")])
        assert f.check_total() is ArithmeticCheck.MISMATCH

    def test_facture_sans_ligne_refusee(self):
        with pytest.raises(ValidationError):
            facture("219.00", [])
