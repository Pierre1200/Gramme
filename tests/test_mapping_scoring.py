"""Le barème du rapprochement doit noter juste avant qu'un normalisateur existe.

Trois normalisateurs factices l'éprouvent : l'oracle qui recopie la vérité, le
prudent qui envoie tout en validation, et le distrait qui compte un carton de
beurre pour un gramme. Si le barème ne les distingue pas, il ne mesure rien.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gramme.facturx import build_xml, read_xml
from gramme.models import Extracted, FieldStatus, ProductMapping, product_key
from gramme.scoring import Verdict, product_spend, score_mappings
from gramme.simulation import mapping_truth, simulate, to_truth
from gramme.truth import load_mapping_truth
from gramme.world import load_world

END = date(2026, 11, 11)


@pytest.fixture(scope="module")
def world():
    return load_world()


@pytest.fixture(scope="module")
def corpus(world):
    return simulate(world, END)


@pytest.fixture(scope="module")
def truths(world):
    return mapping_truth(world)


@pytest.fixture(scope="module")
def spend(corpus):
    return product_spend([to_truth(i, f"{i.number}.pdf") for i in corpus.invoices])


def read[T](type_: type[T], value: T) -> Extracted[T]:
    return Extracted[type_](
        status=FieldStatus.READ, value=value, raw_text=str(value), confidence=1.0
    )


def answer(truth, ingredient: Extracted[str], pack: Extracted[Decimal]) -> ProductMapping:
    return ProductMapping(
        supplier_siren=truth.supplier_siren,
        supplier_name=truth.supplier_name,
        supplier_ref=truth.supplier_ref,
        raw_label=truth.raw_label,
        ingredient=ingredient,
        pack_qty=pack,
    )


def oracle(truth) -> ProductMapping:
    if truth.ingredient is None:
        absent = Extracted[str](status=FieldStatus.ABSENT)
        return answer(truth, absent, Extracted[Decimal](status=FieldStatus.ABSENT))
    return answer(truth, read(str, truth.ingredient), read(Decimal, truth.pack_qty))


def unsure(truth) -> ProductMapping:
    return answer(
        truth,
        Extracted[str](status=FieldStatus.UNREADABLE),
        Extracted[Decimal](status=FieldStatus.UNREADABLE),
    )


class TestTruth:
    def test_the_truth_covers_exactly_the_products_the_invoices_create(self, world, corpus, truths):
        # Les clés que l'écriture en base crée, à partir des factures relues.
        stored = set()
        for invoice in corpus.invoices:
            raw = read_xml(build_xml(world, invoice))
            for line in raw.lines:
                stored.add(
                    product_key(
                        raw.supplier_siren,
                        raw.supplier_name.value,
                        line.supplier_ref,
                        line.raw_label,
                    )
                )
        assert {truth.key for truth in truths} == stored
        assert len(truths) == 66

    def test_a_duplicate_product_is_refused(self, tmp_path, truths):
        entry = truths[0].model_dump(mode="json")
        path = tmp_path / "truth.json"
        path.write_text(json.dumps([entry, entry]), encoding="utf-8")
        with pytest.raises(ValueError, match="produit en double"):
            load_mapping_truth(path)

    def test_a_non_ingredient_cannot_carry_a_pack(self, truths):
        film = next(t for t in truths if t.ingredient is None)
        with pytest.raises(ValidationError):
            answer(film, Extracted[str](status=FieldStatus.ABSENT), read(Decimal, Decimal(1)))


class TestThreeNormalisers:
    def test_the_oracle_is_never_wrong(self, truths, spend):
        score = score_mappings(truths, [oracle(t) for t in truths], spend)
        assert score.wrong_share == 0
        assert score.share(Verdict.CORRECT) == 1
        assert score.products[Verdict.CORRECT] == 66

    def test_sending_everything_to_review_is_never_wrong_but_never_right(self, truths, spend):
        score = score_mappings(truths, [unsure(t) for t in truths], spend)
        assert score.wrong_share == 0
        assert score.share(Verdict.ABSTAINED) == 1

    def test_a_wrong_pack_counts_the_exact_share_of_its_purchases(self, truths, spend):
        def careless(truth):
            if truth.supplier_ref == "160090":  # BEURRE DOUX 82% 10X1KG
                return answer(truth, read(str, truth.ingredient), read(Decimal, Decimal(1)))
            return oracle(truth)

        score = score_mappings(truths, [careless(t) for t in truths], spend)
        butter = next(t for t in truths if t.supplier_ref == "160090")
        total = sum(abs(spend.get(t.key, Decimal(0))) for t in truths)
        assert score.wrong_share == pytest.approx(float(spend[butter.key] / total))
        assert score.products[Verdict.WRONG] == 1
        assert score.mismatches[0].field.endswith("contenu")


class TestVerdicts:
    def test_calling_a_non_ingredient_an_ingredient_is_wrong(self, truths, spend):
        film = next(t for t in truths if t.ingredient is None)
        claim = answer(film, read(str, "Beurre doux 82 %"), read(Decimal, Decimal(1000)))
        score = score_mappings([film], [claim], spend)
        assert score.products[Verdict.WRONG] == 1

    def test_denying_an_ingredient_is_wrong_not_an_abstention(self, truths, spend):
        # Rien ne part en validation : le coût du beurre disparaîtrait en silence.
        butter = next(t for t in truths if t.supplier_ref == "160090")
        denial = answer(
            butter,
            Extracted[str](status=FieldStatus.ABSENT),
            Extracted[Decimal](status=FieldStatus.ABSENT),
        )
        assert score_mappings([butter], [denial], spend).products[Verdict.WRONG] == 1

    def test_a_product_without_answer_is_not_processed(self, truths, spend):
        score = score_mappings(truths, [oracle(t) for t in truths[1:]], spend)
        assert score.not_processed == [truths[0].raw_label]
        assert score.wrong_share == 0
        assert score.not_processed_share > 0

    def test_a_credit_note_reduces_the_spend_of_its_product(self, corpus):
        note = next(i for i in corpus.invoices if i.kind == "credit_note")
        line = note.lines[0]
        key = product_key(note.supplier_siren, note.supplier_name, line.supplier_ref, line.label)
        with_note = product_spend([to_truth(i, "x.pdf") for i in corpus.invoices])
        without = product_spend(
            [to_truth(i, "x.pdf") for i in corpus.invoices if i.kind != "credit_note"]
        )
        assert with_note[key] == without[key] + line.total < without[key]
