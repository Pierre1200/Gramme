"""L'écriture en base ne doit ni perdre, ni dédoubler, ni inventer.

Les factures viennent du jeu factice, lues par le vrai lecteur Factur-X : ce
qui est testé ici est exactement ce qui passera en production. Chaque test
travaille dans une transaction annulée à la fin ; ignorés si Postgres ne
répond pas (`make up`).
"""

from datetime import date
from decimal import Decimal

import psycopg
import pytest

from gramme.config import get_settings
from gramme.db import StoreOutcome, store_invoice
from gramme.facturx import build_xml, read_xml
from gramme.models import Extracted, FieldStatus
from gramme.simulation import simulate
from gramme.world import load_world

END = date(2026, 11, 11)


@pytest.fixture(scope="module")
def world():
    return load_world()


@pytest.fixture(scope="module")
def corpus(world):
    return simulate(world, END)


@pytest.fixture
def conn():
    try:
        connection = psycopg.connect(get_settings().database_url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres injoignable : lancer `make up`")
    # Transaction extérieure annulée quoi qu'il arrive : celles de
    # store_invoice deviennent des points de sauvegarde à l'intérieur.
    with connection, connection.transaction(force_rollback=True):
        yield connection


@pytest.fixture
def read(world, corpus):
    """Une facture du jeu, lue par le lecteur Factur-X."""

    def first(supplier: str, kind: str = "invoice", nth: int = 0):
        invoices = [i for i in corpus.invoices if i.supplier == supplier and i.kind == kind]
        return read_xml(build_xml(world, invoices[nth]))

    return first


def store(conn, invoice, source_hash: str | None = None) -> StoreOutcome:
    number = invoice.invoice_number.value
    return store_invoice(
        conn,
        invoice,
        source_uri=f"test://{number}",
        source_hash=source_hash or f"hash-{number}",
        extracted_by="factur-x",
    )


def scalar(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_an_invoice_is_stored_as_it_was_read(conn, read):
    invoice = read("grossiste")
    assert store(conn, invoice) is StoreOutcome.STORED

    number = invoice.invoice_number.value
    row = conn.execute(
        "SELECT total_ht, total_ttc, extraction_model FROM invoice WHERE invoice_number = %s",
        (number,),
    ).fetchone()
    assert row == (invoice.total_ht.value, invoice.total_ttc.value, "factur-x")

    lines = conn.execute(
        "SELECT raw_label, qty, raw_unit, vat_rate FROM invoice_line il"
        " JOIN invoice i ON i.id = il.invoice_id WHERE i.invoice_number = %s ORDER BY line_no",
        (number,),
    ).fetchall()
    assert lines == [
        (line.raw_label, line.qty.value, line.raw_unit.value, line.vat_rate.value)
        for line in invoice.lines
    ]


def test_a_resent_file_is_not_stored_twice(conn, read):
    invoice = read("boucherie")
    assert store(conn, invoice) is StoreOutcome.STORED
    assert store(conn, invoice) is StoreOutcome.ALREADY_STORED
    assert scalar(conn, "SELECT count(*) FROM invoice") == 1


def test_the_same_number_in_another_file_is_flagged(conn, read):
    invoice = read("boucherie")
    store(conn, invoice)
    assert store(conn, invoice, source_hash="autre-fichier") is StoreOutcome.DUPLICATE_NUMBER
    assert scalar(conn, "SELECT count(*) FROM invoice") == 1


def test_a_credit_note_is_stored_negative(conn, read):
    note = read("boucherie", kind="credit_note")
    assert store(conn, note) is StoreOutcome.STORED
    total, qty, price = conn.execute(
        "SELECT i.total_ht, il.qty, il.unit_price_ht FROM invoice i"
        " JOIN invoice_line il ON il.invoice_id = i.id"
    ).fetchone()
    assert total < 0 and qty < 0 and price > 0


def test_a_footer_discount_keeps_the_invoice_out_of_the_flags(conn, read):
    invoice = read("grossiste")
    store(conn, invoice)
    assert scalar(conn, "SELECT count(*) FROM invoice_adjustment") == len(invoice.adjustments) > 0
    assert not scalar(conn, "SELECT count(*) FROM report_flags WHERE reason = 'total_mismatch'")


def test_a_total_that_does_not_add_up_is_still_flagged(conn, read):
    invoice = read("grossiste")
    wrong = Extracted[Decimal](
        status=FieldStatus.READ, value=invoice.total_ht.value + 10, raw_text="faux", confidence=1.0
    )
    store(conn, invoice.model_copy(update={"total_ht": wrong}))
    assert scalar(conn, "SELECT gap FROM report_flags WHERE reason = 'total_mismatch'") == Decimal(
        "10.00"
    )


def test_products_without_reference_are_not_duplicated(conn, read):
    first, second = read("primeur", nth=0), read("primeur", nth=1)
    store(conn, first)
    store(conn, second)
    labels = {line.raw_label for line in first.lines + second.lines}
    assert scalar(conn, "SELECT count(*) FROM supplier_product") == len(labels)


def test_a_renamed_product_with_the_same_reference_stays_one_product(conn, world, corpus):
    # Le magret change de libellé en cours d'année, sa référence reste B3310.
    magret = [
        i
        for i in corpus.invoices
        if any(line.supplier_ref == "B3310" for line in i.lines) and i.kind == "invoice"
    ]
    before, after = magret[0], magret[-1]
    for invoice in (before, after):
        store(conn, read_xml(build_xml(world, invoice)))

    labels, products = conn.execute(
        "SELECT count(DISTINCT il.raw_label), count(DISTINCT il.supplier_product_id)"
        " FROM invoice_line il JOIN supplier_product sp ON sp.id = il.supplier_product_id"
        " WHERE sp.supplier_ref = 'B3310'"
    ).fetchone()
    assert (labels, products) == (2, 1)


def test_a_supplier_is_recognised_by_its_siren_despite_a_new_name(conn, read):
    first, second = read("cave", nth=0), read("cave", nth=1)
    renamed = Extracted[str](
        status=FieldStatus.READ, value="CAVES DU CANAL", raw_text="CAVES DU CANAL", confidence=1.0
    )
    store(conn, first)
    store(conn, second.model_copy(update={"supplier_name": renamed}))
    assert scalar(conn, "SELECT count(*) FROM supplier") == 1


def test_an_invoice_without_a_date_is_left_for_review(conn, read):
    invoice = read("cave")
    undated = invoice.model_copy(
        update={"invoice_date": Extracted[date](status=FieldStatus.UNREADABLE)}
    )
    assert store(conn, undated) is StoreOutcome.TO_REVIEW
    assert scalar(conn, "SELECT count(*) FROM invoice") == 0
    assert scalar(conn, "SELECT count(*) FROM supplier") == 0


def test_storing_creates_no_price_point(conn, read):
    # Un prix au gramme attend la normalisation : stocker n'en invente aucun.
    store(conn, read("cremerie"))
    assert scalar(conn, "SELECT count(*) FROM price_point") == 0
    assert scalar(conn, "SELECT count(*) FROM supplier_product WHERE status <> 'pending'") == 0
