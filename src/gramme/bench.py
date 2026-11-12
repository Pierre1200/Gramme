"""Le banc d'essai : où en est le corpus, et ce que vaut l'extraction.

Deux usages, parce qu'ils n'arrivent pas au même moment. Pendant la collecte,
seul l'état du corpus a du sens : il dit ce qu'il manque pour que mesurer
veuille dire quelque chose. Une fois les documents lus, le banc note les
extractions contre le jeu de vérité.

Les extractions sont relues sur disque plutôt que recalculées : rejouer le banc
après un changement de barème ne doit ni rappeler le modèle, ni repayer des
tokens.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from gramme.models import ProductMapping, RawInvoice
from gramme.scoring import CorpusScore, InvoiceScore, aggregate, score_invoice
from gramme.truth import MappingTruth, SourceQuality, TruthInvoice, load_mapping_truth

EXTRACTIONS_DIR = Path("data/extractions")

# Le rapprochement se note produit par produit, pas document par document :
# un fichier pour la vérité, un pour les réponses du normalisateur.
MAPPING_TRUTH = Path("mapping/truth.json")
MAPPING_PROPOSALS = Path("mapping/proposals.json")

# Critères de sortie de la collecte, repris de COLLECTE.md. Ce ne sont pas des
# seuils de qualité : ce sont les conditions pour qu'une mesure soit crédible.
TARGET_INVOICES = 10
TEMPORAL_SERIES = 4  # factures d'un même fournisseur, à des dates différentes

# Un corpus sans photo ni manuscrit ne teste pas la promesse de la plaquette.
HARD_QUALITIES = frozenset({SourceQuality.PHOTO, SourceQuality.HANDWRITTEN})


@dataclass(frozen=True)
class CorpusStatus:
    """Ce que contient le jeu de vérité, mesuré contre les critères de sortie."""

    invoices: int
    by_quality: Counter[SourceQuality]
    # Plus longue série de factures d'un même fournisseur à des dates distinctes.
    longest_series: int
    longest_series_supplier: str | None
    with_pitfalls: int
    unreadable_fields: int

    @property
    def hard_documents(self) -> int:
        return sum(self.by_quality[quality] for quality in HARD_QUALITIES)

    @property
    def missing(self) -> list[str]:
        """Ce qui manque encore, en clair. Vide = la collecte peut s'arrêter."""
        gaps: list[str] = []
        if self.invoices < TARGET_INVOICES:
            gaps.append(f"{TARGET_INVOICES - self.invoices} document(s) de plus")
        if self.longest_series < TEMPORAL_SERIES:
            gaps.append(
                f"une série temporelle : {self.longest_series} facture(s) du même "
                f"fournisseur, il en faut {TEMPORAL_SERIES}"
            )
        if not self.hard_documents:
            gaps.append("au moins une photo ou un document manuscrit")
        if not self.with_pitfalls:
            gaps.append("au moins une facture tordue (remise, port, consigne, avoir)")
        return gaps

    @property
    def ready(self) -> bool:
        return not self.missing


def corpus_status(invoices: list[TruthInvoice]) -> CorpusStatus:
    """Compte ce que contient le corpus, sans porter de jugement sur la qualité."""
    by_quality: Counter[SourceQuality] = Counter(invoice.quality for invoice in invoices)

    # Une série temporelle, c'est le même fournisseur à des dates DIFFÉRENTES :
    # quatre factures du même jour ne montrent aucune dérive de prix.
    dates_by_supplier: dict[str, set] = {}
    for invoice in invoices:
        if invoice.supplier_name and invoice.invoice_date:
            dates_by_supplier.setdefault(invoice.supplier_name, set()).add(invoice.invoice_date)

    supplier, longest = None, 0
    for name, dates in sorted(dates_by_supplier.items()):
        if len(dates) > longest:
            supplier, longest = name, len(dates)

    return CorpusStatus(
        invoices=len(invoices),
        by_quality=by_quality,
        longest_series=longest,
        longest_series_supplier=supplier,
        with_pitfalls=sum(1 for invoice in invoices if invoice.pitfalls),
        unreadable_fields=sum(
            len(invoice.unreadable) + sum(len(line.unreadable) for line in invoice.lines)
            for invoice in invoices
        ),
    )


def load_extractions(directory: Path = EXTRACTIONS_DIR) -> dict[str, RawInvoice]:
    """Relit les extractions écrites par `make read`, indexées par document.

    Le nom du fichier n'est pas la clé : c'est `source_file` dans l'annotation
    qui fait foi. Un fichier mal nommé doit apparaître comme non noté, pas
    s'apparier au mauvais document.
    """
    if not directory.is_dir():
        return {}

    extractions: dict[str, RawInvoice] = {}
    for file in sorted(directory.glob("*.json")):
        try:
            extractions[file.stem] = RawInvoice.model_validate_json(
                file.read_text(encoding="utf-8")
            )
        except ValueError as e:
            raise ValueError(f"{file} : extraction invalide\n{e}") from e
    return extractions


def run_bench(
    invoices: list[TruthInvoice], extractions: dict[str, RawInvoice]
) -> tuple[CorpusScore, list[str]]:
    """Note les extractions disponibles, et nomme les documents non extraits.

    Un document sans extraction n'est pas un échec du modèle : c'est un trou
    dans le lot. Le confondre avec une mauvaise lecture fausserait le taux.
    """
    scores: list[InvoiceScore] = []
    not_extracted: list[str] = []

    for invoice in invoices:
        key = Path(invoice.source_file).stem
        extraction = extractions.get(key)
        if extraction is None:
            not_extracted.append(invoice.source_file)
        else:
            scores.append(score_invoice(invoice, extraction))

    return aggregate(scores), not_extracted


def load_mappings(corpus: Path) -> tuple[list[MappingTruth], list[ProductMapping]] | None:
    """La vérité du rapprochement d'un corpus, et les réponses à noter.

    `None` tant qu'aucune vérité n'existe ; une liste de réponses vide tant que
    le normalisateur n'a rien produit.
    """
    truth_file = corpus / MAPPING_TRUTH
    if not truth_file.exists():
        return None
    truths = load_mapping_truth(truth_file)

    proposals_file = corpus / MAPPING_PROPOSALS
    if not proposals_file.exists():
        return truths, []
    entries = json.loads(proposals_file.read_text(encoding="utf-8"))
    try:
        proposals = [ProductMapping.model_validate(entry) for entry in entries]
    except ValueError as e:
        raise ValueError(f"{proposals_file} : réponse invalide\n{e}") from e
    return truths, proposals
