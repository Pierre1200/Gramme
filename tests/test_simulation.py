"""Les factures factices doivent être cohérentes, répétables et crédibles.

Elles servent de vérité à tout ce qui se construit ensuite : une incohérence
ici ne se verrait plus nulle part, puisque c'est contre elles qu'on mesure.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from gramme.simulation import (
    MIN_WEIGHED_QTY,
    gross_needs,
    is_closed,
    money,
    price_on,
    revenue_ht,
    simulate,
    to_truth,
)
from gramme.world import load_world

END = date(2026, 11, 11)


@pytest.fixture(scope="module")
def world():
    return load_world()


@pytest.fixture(scope="module")
def corpus(world):
    return simulate(world, END)


def product(world, supplier: str, ingredient: str):
    return next(p for p in world.suppliers[supplier].products if p.ingredient == ingredient)


class TestNeeds:
    def test_the_gross_need_divides_by_the_yield(self, world):
        # 2 250 g de gambas décortiquées à rendement 0,50 pour 50 parts.
        assert gross_needs(world)["paella"]["gambas"] == Decimal(90)

    def test_needs_propagate_through_sub_recipes(self, world):
        # Une part de tarte = 1/6 de tarte, qui prend 400 g d'une pâte sucrée
        # dont le batch de 1 000 g contient 500 g de farine.
        flour = gross_needs(world)["tarte_citron_part"]["farine_t55"]
        assert flour == pytest.approx(Decimal(500) * 400 / 1000 / 6)


class TestDeterminism:
    def test_the_same_seed_gives_the_same_invoices(self, world, corpus):
        assert simulate(world, END).invoices == corpus.invoices

    def test_another_seed_gives_other_invoices(self, world, corpus):
        assert simulate(world, END, seed=1).invoices != corpus.invoices


class TestPrices:
    def test_the_file_price_applies_on_the_end_date(self, world):
        for supplier in world.suppliers.values():
            for item in supplier.products:
                assert price_on(world, supplier, item, END, END, seed=0) == item.price

    def test_every_supplier_bills_the_file_price_in_the_last_week(self, world, corpus):
        last_week = [i for i in corpus.invoices if (END - i.issued_on).days < 7]
        assert {i.supplier for i in last_week} == set(world.suppliers)
        prices = {p.label: p.price for s in world.suppliers.values() for p in s.products}
        for invoice in last_week:
            for line in invoice.lines:
                if line.label in prices:
                    assert line.unit_price == prices[line.label]

    def test_butter_was_18_percent_cheaper_before_the_rise(self, world):
        butter = product(world, "grossiste", "beurre")
        before = price_on(
            world, world.suppliers["grossiste"], butter, END - timedelta(weeks=16), END, 0
        )
        assert before == money(butter.price / Decimal("1.18"))

    def test_a_list_price_holds_for_the_whole_month(self, world):
        grossiste = world.suppliers["grossiste"]
        butter = product(world, "grossiste", "beurre")
        first, last = date(2026, 10, 1), date(2026, 10, 31)
        assert price_on(world, grossiste, butter, first, END, 0) == price_on(
            world, grossiste, butter, last, END, 0
        )


class TestInvoices:
    def test_nothing_is_delivered_during_a_closure(self, world, corpus):
        assert not [
            i for i in corpus.invoices if i.kind == "invoice" and is_closed(world, i.issued_on)
        ]

    def test_packs_stay_whole_and_weighed_goods_do_not(self, world, corpus):
        weighed = {
            label
            for s in world.suppliers.values()
            for p in s.products
            if p.weight == "variable"
            for label in (p.label, p.relabel.was if p.relabel else p.label)
        }
        for invoice in corpus.invoices:
            for line in invoice.lines:
                if line.label in weighed:
                    assert abs(line.qty) >= MIN_WEIGHED_QTY
                else:
                    assert line.qty == line.qty.to_integral_value()

    def test_a_footer_discount_breaks_the_sum_of_lines(self, corpus):
        invoice = next(i for i in corpus.invoices if i.supplier == "grossiste")
        discount = invoice.lines_total - invoice.total_ht
        # Arrondie taux par taux : à un centime près de 2 % du total.
        assert discount == pytest.approx(invoice.lines_total * Decimal("0.02"), abs=Decimal("0.01"))
        assert invoice.pitfalls

    def test_vat_is_computed_rate_by_rate(self, corpus):
        invoice = next(
            i for i in corpus.invoices if i.supplier == "grossiste" and len(i.vat_by_rate) > 1
        )
        expected = Decimal(0)
        for rate in invoice.vat_by_rate:
            base = sum(line.total for line in invoice.lines if line.vat_rate == rate)
            base += sum(a.amount for a in invoice.adjustments if a.vat_rate == rate)
            expected += money(base * rate / 100)
        assert invoice.total_ttc - invoice.total_ht == expected

    def test_a_credit_note_is_negative_and_names_its_invoice(self, corpus):
        numbers = {i.number for i in corpus.invoices}
        credit_notes = [i for i in corpus.invoices if i.kind == "credit_note"]
        assert credit_notes
        for note in credit_notes:
            assert note.total_ht < 0 and note.total_ttc < note.total_ht
            assert note.credits in numbers

    def test_shipping_is_charged_only_below_the_threshold(self, world, corpus):
        shipping = world.suppliers["patisserie"].shipping
        invoices = [i for i in corpus.invoices if i.supplier == "patisserie"]
        charged = [i for i in invoices if i.adjustments]
        assert charged and len(charged) < len(invoices)
        for invoice in invoices:
            assert bool(invoice.adjustments) == (invoice.lines_total < shipping.free_above)

    def test_returned_crates_match_the_previous_delivery(self, corpus):
        delivered = None
        for invoice in (i for i in corpus.invoices if i.supplier == "cave"):
            crates = {line.label: line.qty for line in invoice.lines if "CONSIGNE" in line.label}
            if not crates:
                continue
            returned = crates.get("RETOUR CONSIGNE CASIER 12 BOUT.")
            assert returned == (-delivered if delivered else None)
            delivered = crates["CONSIGNE CASIER 12 BOUT."]

    def test_a_relabelled_product_changes_name_once(self, corpus):
        labels = [
            line.label
            for invoice in corpus.invoices
            for line in invoice.lines
            if line.ingredient == "tomate"
        ]
        switch = labels.index("TOMATE RONDE MAROC 67-82 COLIS 6KG")
        assert set(labels[:switch]) == {"TOMATE RONDE FRANCE 67-82 COLIS 6KG"}
        assert set(labels[switch:]) == {"TOMATE RONDE MAROC 67-82 COLIS 6KG"}

    def test_a_supplier_without_article_references_bills_none(self, corpus):
        lines = [line for i in corpus.invoices if i.supplier == "primeur" for line in i.lines]
        assert lines and all(line.supplier_ref is None for line in lines)


class TestRealism:
    def test_a_year_gives_a_few_hundred_invoices(self, corpus):
        assert 380 <= len(corpus.invoices) <= 520

    def test_purchases_stay_a_plausible_share_of_sales(self, world, corpus):
        purchases = sum(i.total_ht for i in corpus.invoices)
        assert Decimal("0.18") <= purchases / revenue_ht(world, corpus) <= Decimal("0.35")

    def test_some_invoices_are_resent(self, corpus):
        numbers = {i.number for i in corpus.invoices if i.kind == "invoice"}
        assert corpus.resent and set(corpus.resent) <= numbers


class TestTruth:
    def test_every_invoice_becomes_a_valid_annotation(self, corpus):
        for invoice in corpus.invoices:
            truth = to_truth(invoice, f"{invoice.number}.pdf")
            assert truth.total_ht == invoice.total_ht
            assert len(truth.lines) == len(invoice.lines)

    def test_a_hidden_pack_is_spelt_out(self, corpus):
        invoice = next(i for i in corpus.invoices if i.supplier == "cremerie")
        milk = next(
            line
            for line in to_truth(invoice, "a.pdf").lines
            if line.raw_label == "LAIT ENTIER UHT 6X1L"
        )
        assert (milk.pack_qty, milk.base_unit) == (Decimal(6000), "ml")
        assert "colisage" in milk.notes
