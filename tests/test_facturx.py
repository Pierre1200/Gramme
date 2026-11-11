"""Le XML Factur-X doit dire exactement ce que dit la facture simulée.

Le XML fait foi : c'est lui que Gramme lira. Un écart entre lui et le jeu de
vérité fausserait toute mesure sans jamais lever d'erreur.
"""

import io
from datetime import date
from decimal import Decimal

import facturx as library
import pytest
from pypdf import PdfWriter

from gramme.facturx import (
    NotFacturX,
    build_xml,
    embed,
    extract_xml,
    read_invoice,
    read_xml,
    unit_code,
)
from gramme.models import ArithmeticCheck
from gramme.scoring import score_invoice
from gramme.simulation import simulate, to_truth
from gramme.world import load_world


@pytest.fixture(scope="module")
def world():
    return load_world()


@pytest.fixture(scope="module")
def corpus(world):
    return simulate(world, date(2026, 11, 11))


def read(world, invoice) -> dict:
    return library.parse_ubl_cii_xml(build_xml(world, invoice), check_xsd=False)


def test_every_invoice_is_valid_against_the_official_schema(world, corpus):
    for invoice in corpus.invoices:
        assert build_xml(world, invoice)  # lève si le schéma refuse le XML


def test_the_xml_carries_the_totals_of_the_invoice(world, corpus):
    invoice = next(i for i in corpus.invoices if i.supplier == "grossiste")
    data = read(world, invoice)
    assert data["BT-1"] == invoice.number
    assert Decimal(data["BT-109"]) == invoice.total_ht
    assert Decimal(data["BT-112"]) == invoice.total_ttc
    assert len(data["BG-25"]) == len(invoice.lines)
    assert Decimal(data["BT-107"]) == invoice.lines_total - invoice.total_ht


def test_a_credit_note_is_written_in_positive_amounts(world, corpus):
    note = next(i for i in corpus.invoices if i.kind == "credit_note")
    data = read(world, note)
    assert data["BT-3"] == "381"
    assert Decimal(data["BT-109"]) == -note.total_ht > 0
    assert data["BG-3"] == [{"BT-25": note.credits}]


def test_a_printed_unit_becomes_a_standard_code(world, corpus):
    assert unit_code("KG") == "KGM"
    with pytest.raises(KeyError):
        unit_code("POIGNEE")


def blank_pdf() -> bytes:
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(buffer)
    return buffer.getvalue()


def test_the_xml_survives_the_trip_through_a_pdf(world, corpus):
    xml = build_xml(world, corpus.invoices[0])
    pdf = embed(blank_pdf(), xml, title="Facture", author="Fournisseur fictif")
    assert extract_xml(pdf) == xml


class TestReader:
    def test_every_invoice_is_read_back_exactly(self, world, corpus):
        # Le barème lui-même fait la comparaison : aucun champ faux, aucune
        # ligne perdue ou inventée, rien à faire relire par un humain.
        for invoice in corpus.invoices:
            truth = to_truth(invoice, f"{invoice.number}.pdf", unit_of=unit_code)
            score = score_invoice(truth, read_xml(build_xml(world, invoice)))
            assert not score.mismatches, (invoice.number, score.mismatches)
            assert not score.missing_lines and not score.invented_lines
            assert score.to_review == 0

    def test_every_invoice_read_back_is_arithmetically_consistent(self, world, corpus):
        for invoice in corpus.invoices:
            got = read_xml(build_xml(world, invoice))
            assert got.check_total() is ArithmeticCheck.OK, invoice.number
            for line in got.lines:
                assert line.check_arithmetic() is ArithmeticCheck.OK, (invoice.number, line)

    def test_a_credit_note_is_read_back_negative(self, world, corpus):
        note = next(i for i in corpus.invoices if i.kind == "credit_note")
        got = read_xml(build_xml(world, note))
        assert got.total_ht.value == note.total_ht < 0
        assert got.lines[0].qty.value < 0
        assert got.lines[0].unit_price_ht.value > 0  # un prix ne devient jamais négatif

    def test_the_supplier_siren_and_line_vat_rates_are_read(self, world, corpus):
        invoice = next(i for i in corpus.invoices if i.supplier == "grossiste")
        got = read_xml(build_xml(world, invoice))
        assert got.supplier_siren == world.suppliers["grossiste"].siren
        assert [line.vat_rate.value for line in got.lines] == [
            line.vat_rate for line in invoice.lines
        ]

    def test_the_article_reference_is_kept_when_there_is_one(self, world, corpus):
        with_refs = next(i for i in corpus.invoices if i.supplier == "boucherie")
        without = next(i for i in corpus.invoices if i.supplier == "primeur")
        assert read_xml(build_xml(world, with_refs)).lines[0].supplier_ref == "B1012"
        assert read_xml(build_xml(world, without)).lines[0].supplier_ref is None

    def test_a_footer_discount_is_read_as_a_negative_adjustment(self, world, corpus):
        invoice = next(i for i in corpus.invoices if i.supplier == "grossiste")
        got = read_xml(build_xml(world, invoice))
        assert [a.amount_ht.value for a in got.adjustments] == [
            a.amount for a in invoice.adjustments
        ]

    def test_a_pdf_is_read_like_its_xml(self, world, corpus):
        xml = build_xml(world, corpus.invoices[0])
        pdf = embed(blank_pdf(), xml, title="Facture", author="Fournisseur fictif")
        assert read_invoice(pdf) == read_xml(xml)

    @pytest.mark.parametrize("document", [blank_pdf(), b"une photo, pas un PDF"])
    def test_a_document_without_xml_is_named_as_such(self, document):
        with pytest.raises(NotFacturX):
            read_invoice(document)
