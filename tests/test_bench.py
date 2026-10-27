"""Le banc doit dire la vérité sur l'état du corpus, pas rassurer.

Ces tests portent surtout sur ce qui manque : un corpus incomplet qui se
déclarerait prêt ferait croire qu'une mesure est crédible alors qu'elle ne
l'est pas.
"""

import json
from datetime import date
from decimal import Decimal

import pytest

from gramme.bench import corpus_status, load_extractions, run_bench
from gramme.models import Extracted, FieldStatus, RawInvoice, RawInvoiceLine
from gramme.truth import SourceQuality, TruthInvoice, TruthLine


def truth(
    source_file: str = "a.jpg",
    quality: SourceQuality = SourceQuality.PDF,
    supplier: str | None = "METRO",
    day: int | None = 12,
    pitfalls: list[str] | None = None,
    unreadable: list[str] | None = None,
) -> TruthInvoice:
    return TruthInvoice(
        source_file=source_file,
        quality=quality,
        supplier_name=supplier,
        invoice_date=date(2026, 8, day) if day else None,
        total_ht=None if unreadable else Decimal("100.00"),
        lines=[TruthLine(raw_label="BEURRE", qty=Decimal("1"))],
        pitfalls=pitfalls or [],
        unreadable=unreadable or [],
    )


def extraction(label: str = "BEURRE") -> RawInvoice:
    def read(value):
        return Extracted[type(value)](
            status=FieldStatus.READ, value=value, raw_text=str(value), confidence=0.95
        )

    return RawInvoice(
        supplier_name=read("METRO"),
        invoice_number=Extracted[str](status=FieldStatus.ABSENT),
        invoice_date=read(date(2026, 8, 12)),
        total_ht=read(Decimal("100.00")),
        total_ttc=read(Decimal("110.00")),
        lines=[
            RawInvoiceLine(
                raw_label=label,
                qty=read(Decimal("1")),
                raw_unit=read("KG"),
                unit_price_ht=read(Decimal("1")),
                line_total_ht=read(Decimal("1")),
            )
        ],
    )


class TestCorpusStatus:
    def test_an_empty_corpus_is_not_ready(self):
        status = corpus_status([])
        assert status.invoices == 0
        assert not status.ready
        assert len(status.missing) == 4  # volume, série, document difficile, facture tordue

    def test_it_counts_by_quality(self):
        status = corpus_status(
            [
                truth("a.pdf", SourceQuality.PDF),
                truth("b.jpg", SourceQuality.PHOTO),
                truth("c.jpg", SourceQuality.PHOTO),
            ]
        )
        assert status.by_quality[SourceQuality.PHOTO] == 2
        assert status.hard_documents == 2

    def test_a_temporal_series_needs_different_dates(self):
        # Quatre factures du même fournisseur le même jour ne montrent aucune
        # dérive de prix : ce n'est pas une série.
        same_day = [truth(f"{i}.pdf", day=12) for i in range(4)]
        assert corpus_status(same_day).longest_series == 1

        spread = [truth(f"{i}.pdf", day=1 + i * 7) for i in range(4)]
        status = corpus_status(spread)
        assert status.longest_series == 4
        assert status.longest_series_supplier == "METRO"

    def test_a_supplier_without_a_date_is_not_counted(self):
        assert corpus_status([truth(day=None)]).longest_series == 0

    def test_it_counts_pitfalls_and_unreadable_fields(self):
        status = corpus_status(
            [
                truth("a.pdf", pitfalls=["remise de pied de page"]),
                truth("b.pdf", unreadable=["total_ht"]),
            ]
        )
        assert status.with_pitfalls == 1
        assert status.unreadable_fields == 1

    def test_a_complete_corpus_is_ready(self):
        invoices = [
            truth(f"{i}.jpg", SourceQuality.PHOTO, day=1 + i * 3, pitfalls=["remise"])
            for i in range(10)
        ]
        status = corpus_status(invoices)
        assert status.ready
        assert status.missing == []

    def test_volume_alone_is_not_enough(self):
        # Dix PDF propres du même jour : le compte y est, la mesure ne vaudrait
        # rien pour autant.
        invoices = [truth(f"{i}.pdf", SourceQuality.PDF, day=12) for i in range(10)]
        status = corpus_status(invoices)
        assert status.invoices == 10
        assert not status.ready


class TestLoadExtractions:
    def test_a_missing_directory_gives_nothing(self, tmp_path):
        assert load_extractions(tmp_path / "absent") == {}

    def test_it_loads_by_file_stem(self, tmp_path):
        (tmp_path / "a.json").write_text(extraction().model_dump_json(), encoding="utf-8")
        assert list(load_extractions(tmp_path)) == ["a"]

    def test_an_invalid_extraction_names_the_file(self, tmp_path):
        (tmp_path / "broken.json").write_text(json.dumps({"lines": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="broken.json"):
            load_extractions(tmp_path)


class TestRunBench:
    def test_a_document_without_an_extraction_is_not_scored(self, tmp_path):
        # Un trou dans le lot n'est pas une mauvaise lecture : le confondre
        # ferait chuter le taux pour une raison qui n'est pas le modèle.
        corpus, not_extracted = run_bench([truth("a.jpg"), truth("b.jpg")], {"a": extraction()})
        assert not_extracted == ["b.jpg"]
        assert len(corpus.invoices) == 1

    def test_it_scores_what_it_can(self):
        corpus, not_extracted = run_bench([truth("a.jpg")], {"a": extraction()})
        assert not not_extracted
        assert corpus.correct_rate > 0

    def test_a_misread_label_shows_as_a_missing_line(self):
        corpus, _ = run_bench([truth("a.jpg")], {"a": extraction(label="BEURE")})
        assert corpus.missing_lines == 1
        assert corpus.invented_lines == 1

    def test_nothing_to_score(self):
        corpus, not_extracted = run_bench([truth("a.jpg")], {})
        assert not_extracted == ["a.jpg"]
        assert corpus.wrong_rate == 0.0  # pas de division par zéro
