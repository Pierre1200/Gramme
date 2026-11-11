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

from gramme.facturx import build_xml, embed, extract_xml, unit_code
from gramme.simulation import simulate
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


def test_the_xml_survives_the_trip_through_a_pdf(world, corpus):
    xml = build_xml(world, corpus.invoices[0])
    blank = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(blank)
    pdf = embed(blank.getvalue(), xml, title="Facture", author="Fournisseur fictif")
    assert extract_xml(pdf) == xml
