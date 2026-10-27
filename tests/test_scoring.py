"""Le barème est la seule chose que le banc ne mesure pas.

Si `score_field` se trompe, tous les scores futurs sont faux et rien ne le
signalera. La matrice 3×3 est donc verrouillée cas par cas, explicitement.
"""

from datetime import date
from decimal import Decimal

import pytest

from gramme.models import Extracted, FieldStatus, RawInvoice, RawInvoiceLine
from gramme.scoring import (
    LINE_FIELDS,
    Verdict,
    aggregate,
    match_lines,
    score_field,
    score_invoice,
)
from gramme.truth import SourceQuality, TruthInvoice, TruthLine, VatBasis


def read(value, confidence: float = 0.95) -> Extracted:
    """Un champ lu par le modèle, avec la valeur donnée."""
    return Extracted[type(value)](
        status=FieldStatus.READ, value=value, raw_text=str(value), confidence=confidence
    )


UNREADABLE = Extracted[Decimal](status=FieldStatus.UNREADABLE, raw_text="8,4?")
ABSENT = Extracted[Decimal](status=FieldStatus.ABSENT)


class TestTruthMatrix:
    """Les neuf cas de vérité × réponse, un test chacun."""

    # --- Le document porte une valeur lisible -----------------------------
    def test_a_well_read_value_is_correct(self):
        assert score_field(Decimal("8.47"), False, read(Decimal("8.47"))) is Verdict.CORRECT

    def test_a_misread_value_is_wrong(self):
        # Le cas qui corrompt price_point, qui est append-only.
        assert score_field(Decimal("8.74"), False, read(Decimal("8.47"))) is Verdict.WRONG

    def test_an_unread_value_is_an_abstention(self):
        # Le modèle ne sait pas et le dit : ça part en validation humaine.
        assert score_field(Decimal("8.47"), False, UNREADABLE) is Verdict.ABSTAINED

    def test_denying_an_existing_value_is_wrong(self):
        # ABSENT ne déclenche aucune relecture : la valeur est perdue en
        # silence. C'est pire qu'une abstention, donc c'est faux.
        assert score_field(Decimal("8.47"), False, ABSENT) is Verdict.WRONG

    # --- Le champ est présent mais illisible ------------------------------
    def test_an_unreadable_field_recognised_as_such(self):
        assert score_field(None, True, UNREADABLE) is Verdict.CORRECT

    def test_guessing_an_unreadable_field_is_wrong(self):
        # Le pire cas au regard de la décision 5 : il ne peut pas savoir, et
        # il affirme quand même.
        assert score_field(None, True, read(Decimal("8.47"))) is Verdict.WRONG

    def test_denying_an_unreadable_field_is_wrong(self):
        assert score_field(None, True, ABSENT) is Verdict.WRONG

    # --- Le document ne porte pas ce champ --------------------------------
    def test_an_absent_field_recognised_as_such(self):
        assert score_field(None, False, ABSENT) is Verdict.CORRECT

    def test_hallucinating_an_absent_field_is_wrong(self):
        assert score_field(None, False, read(Decimal("8.47"))) is Verdict.WRONG

    def test_flagging_an_absent_field_unreadable_is_an_abstention(self):
        # Coûte du temps humain pour rien, mais ne corrompt rien.
        assert score_field(None, False, UNREADABLE) is Verdict.ABSTAINED


class TestValueComparison:
    def test_decimals_compare_by_value(self):
        assert score_field(Decimal("2"), False, read(Decimal("2.00"))) is Verdict.CORRECT

    def test_text_ignores_case_and_spacing(self):
        assert score_field("BEURRE  DOUX", False, read("beurre doux")) is Verdict.CORRECT

    def test_accents_are_significant(self):
        # « CREME » lu au lieu de « CRÈME » est une vraie faiblesse de lecture :
        # la masquer reviendrait à saboter la mesure.
        assert score_field("CRÈME", False, read("CREME")) is Verdict.WRONG

    def test_dates(self):
        assert score_field(date(2026, 8, 12), False, read(date(2026, 8, 12))) is Verdict.CORRECT


class TestLineMatching:
    def _truth(self, label: str) -> TruthLine:
        return TruthLine(raw_label=label)

    def _read(self, label: str) -> RawInvoiceLine:
        return RawInvoiceLine(
            raw_label=label,
            qty=read(Decimal("1")),
            raw_unit=read("KG"),
            unit_price_ht=read(Decimal("1")),
            line_total_ht=read(Decimal("1")),
        )

    def test_a_different_order_changes_nothing(self):
        pairs, missing, invented = match_lines(
            [self._truth("BEURRE"), self._truth("CREME")],
            [self._read("CREME"), self._read("BEURRE")],
        )
        assert len(pairs) == 2 and not missing and not invented

    def test_one_missed_line_does_not_shift_the_others(self):
        # Le vrai intérêt de l'appariement par libellé : en positionnel, ce cas
        # produirait trois erreurs au lieu d'une seule ligne manquante.
        pairs, missing, invented = match_lines(
            [self._truth("A"), self._truth("B"), self._truth("C"), self._truth("D")],
            [self._read("A"), self._read("C"), self._read("D")],
        )
        assert len(pairs) == 3
        assert [line.raw_label for line in missing] == ["B"]
        assert not invented

    def test_duplicate_labels(self):
        # Deux lignes du même article à deux prix : courant sur une facture.
        pairs, missing, invented = match_lines(
            [self._truth("BEURRE"), self._truth("BEURRE")],
            [self._read("BEURRE"), self._read("BEURRE")],
        )
        assert len(pairs) == 2 and not missing and not invented

    def test_an_invented_line(self):
        _, missing, invented = match_lines([self._truth("A")], [self._read("A"), self._read("Z")])
        assert not missing
        assert [line.raw_label for line in invented] == ["Z"]


def truth_invoice(**overrides) -> TruthInvoice:
    base = dict(
        source_file="f.jpg",
        quality=SourceQuality.PHOTO,
        vat_basis=VatBasis.EXCLUDING,
        supplier_name="METRO",
        invoice_number="FA-1187",
        invoice_date=date(2026, 8, 12),
        total_ht=Decimal("100.00"),
        total_ttc=Decimal("110.00"),
        lines=[
            TruthLine(
                raw_label="BEURRE",
                qty=Decimal("2"),
                raw_unit="KG",
                unit_price_ht=Decimal("8.47"),
                line_total_ht=Decimal("16.94"),
            )
        ],
    )
    return TruthInvoice(**(base | overrides))


def read_invoice(**overrides) -> RawInvoice:
    base = dict(
        supplier_name=read("METRO"),
        invoice_number=read("FA-1187"),
        invoice_date=read(date(2026, 8, 12)),
        total_ht=read(Decimal("100.00")),
        total_ttc=read(Decimal("110.00")),
        lines=[
            RawInvoiceLine(
                raw_label="BEURRE",
                qty=read(Decimal("2")),
                raw_unit=read("KG"),
                unit_price_ht=read(Decimal("8.47")),
                line_total_ht=read(Decimal("16.94")),
            )
        ],
    )
    return RawInvoice(**(base | overrides))


class TestScoreInvoice:
    def test_a_perfect_extraction(self):
        score = score_invoice(truth_invoice(), read_invoice())
        assert score.verdicts[Verdict.WRONG] == 0
        assert score.wrong_rate == 0.0
        assert score.fields_scored == 9  # 5 d'en-tête + 4 sur l'unique ligne
        assert not score.mismatches

    def test_a_wrong_price_is_reported_with_both_values(self):
        got = read_invoice(
            lines=[
                RawInvoiceLine(
                    raw_label="BEURRE",
                    qty=read(Decimal("2")),
                    raw_unit=read("KG"),
                    unit_price_ht=read(Decimal("8.74")),
                    line_total_ht=read(Decimal("16.94")),
                )
            ]
        )
        score = score_invoice(truth_invoice(), got)
        assert score.verdicts[Verdict.WRONG] == 1
        (mismatch,) = score.mismatches
        assert mismatch.field == "ligne[0].unit_price_ht"
        assert (mismatch.expected, mismatch.got) == ("8.47", "8.74")

    def test_a_missing_line_is_counted_separately(self):
        expected = truth_invoice(
            lines=[TruthLine(raw_label="BEURRE"), TruthLine(raw_label="CREME")]
        )
        score = score_invoice(expected, read_invoice())
        assert score.missing_lines == ["CREME"]

    def test_low_confidence_fields_count_as_cost_not_error(self):
        # Corrects mais sous le seuil : justes, donc pas d'erreur — mais ils
        # coûtent du temps humain, compté séparément.
        got = read_invoice(total_ht=read(Decimal("100.00"), confidence=0.4))
        score = score_invoice(expected := truth_invoice(), got, review_threshold=0.90)
        assert score.verdicts[Verdict.WRONG] == 0
        assert score.to_review == 1
        assert expected.total_ht == Decimal("100.00")


class TestAggregate:
    def test_breakdown_by_quality(self):
        photo = score_invoice(truth_invoice(), read_invoice())
        pdf = score_invoice(
            truth_invoice(source_file="g.pdf", quality=SourceQuality.PDF), read_invoice()
        )
        corpus = aggregate([photo, pdf])
        assert set(corpus.by_quality) == {SourceQuality.PHOTO, SourceQuality.PDF}
        assert corpus.correct_rate == 1.0
        assert corpus.wrong_rate == 0.0

    def test_an_empty_corpus_does_not_divide_by_zero(self):
        corpus = aggregate([])
        assert corpus.wrong_rate == 0.0 and corpus.correct_rate == 0.0


@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.9, 1.0])
def test_the_review_threshold_never_changes_a_verdict(threshold):
    """Justesse et coût sont orthogonaux : le seuil ne bouge que le second."""
    score = score_invoice(truth_invoice(), read_invoice(), review_threshold=threshold)
    assert score.verdicts[Verdict.WRONG] == 0
    assert score.verdicts[Verdict.CORRECT] == 9


class TestMissedLines:
    """Une ligne perdue ou inventée doit peser dans le taux de faux.

    Régression corrigée : elles étaient comptées à côté du taux, qui restait
    donc flatteur pendant que l'extraction perdait la facture.
    """

    def _truth_with_ten_lines(self) -> TruthInvoice:
        return truth_invoice(
            lines=[
                TruthLine(
                    raw_label=f"ARTICLE {i}",
                    qty=Decimal("1"),
                    raw_unit="KG",
                    unit_price_ht=Decimal("10"),
                    line_total_ht=Decimal("10"),
                )
                for i in range(10)
            ]
        )

    def _reading_with(self, count: int) -> RawInvoice:
        return read_invoice(
            lines=[
                RawInvoiceLine(
                    raw_label=f"ARTICLE {i}",
                    qty=read(Decimal("1")),
                    raw_unit=read("KG"),
                    unit_price_ht=read(Decimal("10")),
                    line_total_ht=read(Decimal("10")),
                )
                for i in range(count)
            ]
        )

    def test_losing_nine_lines_out_of_ten_cannot_look_good(self):
        score = score_invoice(self._truth_with_ten_lines(), self._reading_with(1))
        assert len(score.missing_lines) == 9
        assert score.wrong_rate > 0.5  # avant correction : 11 %

    def test_invented_lines_are_penalised(self):
        score = score_invoice(self._truth_with_ten_lines(), self._reading_with(15))
        assert len(score.invented_lines) == 5
        assert score.verdicts[Verdict.WRONG] >= 5 * len(LINE_FIELDS)

    def test_a_perfect_extraction_stays_at_zero(self):
        # Le correctif ne doit pas inventer de faux quand tout va bien.
        score = score_invoice(self._truth_with_ten_lines(), self._reading_with(10))
        assert score.wrong_rate == 0.0
        assert score.critical_wrong == 0

    def test_a_missed_line_appears_among_the_mismatches(self):
        score = score_invoice(self._truth_with_ten_lines(), self._reading_with(9))
        assert "ligne manquante" in [m.field for m in score.mismatches]


class TestCriticalFields:
    def test_a_wrong_price_is_critical(self):
        got = read_invoice(
            lines=[
                RawInvoiceLine(
                    raw_label="BEURRE",
                    qty=read(Decimal("2")),
                    raw_unit=read("KG"),
                    unit_price_ht=read(Decimal("8.74")),
                    line_total_ht=read(Decimal("16.94")),
                )
            ]
        )
        assert score_invoice(truth_invoice(), got).critical_wrong == 1

    def test_a_wrong_invoice_number_is_not(self):
        # Rattrapable : il ne part pas dans price_point.
        score = score_invoice(truth_invoice(), read_invoice(invoice_number=read("FA-AUTRE")))
        assert score.verdicts[Verdict.WRONG] == 1
        assert score.critical_wrong == 0
