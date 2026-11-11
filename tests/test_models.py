"""Les invariants des contrats d'extraction.

Ce qui est testé ici, ce n'est pas Pydantic : c'est la promesse qu'un champ
ne peut pas porter une valeur sans l'assumer, et qu'aucun montant n'est
reconstruit à partir des autres.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from gramme.models import (
    INVOICE_TOTAL_TOLERANCE,
    ArithmeticCheck,
    Extracted,
    FieldStatus,
    RawInvoice,
    RawInvoiceLine,
)


def read[T](type_: type[T], value: T, confidence: float = 0.95) -> Extracted[T]:
    return Extracted[type_](
        status=FieldStatus.READ, value=value, raw_text=str(value), confidence=confidence
    )


def line(qty: str, unit_price: str, total: str) -> RawInvoiceLine:
    return RawInvoiceLine(
        raw_label="BEURRE DOUX PLAQUE 25KG",
        qty=read(Decimal, Decimal(qty)),
        raw_unit=read(str, "KG"),
        unit_price_ht=read(Decimal, Decimal(unit_price)),
        line_total_ht=read(Decimal, Decimal(total)),
    )


def invoice(total_ht: str, lines: list[RawInvoiceLine]) -> RawInvoice:
    return RawInvoice(
        supplier_name=read(str, "METRO"),
        invoice_number=read(str, "FA-2026-1187"),
        invoice_date=read(date, date(2026, 8, 12)),
        total_ht=read(Decimal, Decimal(total_ht)),
        total_ttc=read(Decimal, Decimal(total_ht) * Decimal("1.055")),
        lines=lines,
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
    def test_inconsistent_states_are_refused(self, kwargs):
        with pytest.raises(ValidationError):
            Extracted[Decimal](**kwargs)

    def test_an_absent_field_needs_no_review(self):
        # Un champ que le document ne porte pas n'est pas une erreur : il ne
        # doit pas polluer la file de validation.
        assert Extracted[Decimal](status=FieldStatus.ABSENT).needs_review(0.9) is False

    def test_an_unreadable_field_always_needs_review(self):
        assert Extracted[Decimal](status=FieldStatus.UNREADABLE).needs_review(0.0) is True

    def test_confidence_below_the_threshold(self):
        assert read(Decimal, Decimal(1), confidence=0.7).needs_review(0.9) is True
        assert read(Decimal, Decimal(1), confidence=0.95).needs_review(0.9) is False

    def test_it_is_immutable(self):
        with pytest.raises(ValidationError):
            read(Decimal, Decimal(1)).confidence = 0.1


class TestLineArithmetic:
    def test_a_supplier_rounding_is_accepted(self):
        # 12,5 × 8,47 = 105,875, imprimé 105,88.
        assert line("12.5", "8.47", "105.88").check_arithmetic() is ArithmeticCheck.OK

    def test_an_inconsistency_is_detected(self):
        assert line("12.5", "8.47", "158.88").check_arithmetic() is ArithmeticCheck.MISMATCH

    def test_an_unreadable_field_reconstructs_nothing(self):
        broken = RawInvoiceLine(
            raw_label="X",
            qty=read(Decimal, Decimal("12.5")),
            raw_unit=read(str, "KG"),
            unit_price_ht=Extracted[Decimal](status=FieldStatus.UNREADABLE, raw_text="8,4?"),
            line_total_ht=read(Decimal, Decimal("105.88")),
        )
        assert broken.check_arithmetic() is ArithmeticCheck.INCOMPLETE
        assert broken.unit_price_ht.value is None  # surtout pas 8.47 déduit du total


class TestInvoiceArithmetic:
    def test_a_consistent_total(self):
        got = invoice("219.00", [line("1", "100.00", "100.00"), line("1", "119.00", "119.00")])
        assert got.check_total() is ArithmeticCheck.OK

    def test_a_missing_line_is_detected(self):
        # Le cas qu'aucune enveloppe par champ ne peut voir : bas de page coupé.
        got = invoice("219.00", [line("1", "100.00", "100.00")])
        assert got.check_total() is ArithmeticCheck.MISMATCH

    def test_an_invoice_without_lines_is_refused(self):
        with pytest.raises(ValidationError):
            invoice("219.00", [])


def test_the_sql_tolerance_follows_the_python_one():
    """La vue `report_flags` réécrit la tolérance en dur.

    Les deux valeurs doivent bouger ensemble : ce test casse si l'une change
    sans l'autre.
    """
    sql = Path("db/migrations/002_rapport.sql").read_text(encoding="utf-8")
    assert f"> {INVOICE_TOTAL_TOLERANCE}" in sql
