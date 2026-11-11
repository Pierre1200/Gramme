"""Le barème : ce que « réussir » veut dire, défini avant d'avoir un résultat.

Écrit avant `extract.py` délibérément : une fois la sortie d'un modèle sous les
yeux, on rationalise.

Toutes les erreurs ne coûtent pas la même chose. Un montant faux entre dans
`price_point`, qui est append-only : la donnée est corrompue pour toujours ; une
abstention coûte trente secondes de relecture. D'où la règle du module : une
réponse qui n'atteint pas l'humain et qui n'est pas juste est fausse. Répondre
ABSENT sur un champ illisible l'est donc — `needs_review()` renvoie False, donc
rien ne part en validation.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from gramme.models import Extracted, FieldStatus, RawInvoice, RawInvoiceLine
from gramme.truth import SourceQuality, TruthInvoice, TruthLine

# Champs comparés, par niveau. `raw_label` n'y figure pas : il sert de clé
# d'appariement des lignes, il est donc évalué par l'appariement lui-même.
HEADER_FIELDS = ("supplier_name", "invoice_number", "invoice_date", "total_ht", "total_ttc")
LINE_FIELDS = ("qty", "raw_unit", "unit_price_ht", "line_total_ht")

# Ceux dont une erreur corrompt un prix. Les autres (raw_unit, invoice_number)
# sont utiles mais rattrapables. Comptés à part plutôt que pondérés dans une
# moyenne : une moyenne masquerait lequel a cassé.
CRITICAL_FIELDS = frozenset({"qty", "unit_price_ht", "line_total_ht", "total_ht", "invoice_date"})

# Combien de champs critiques disparaissent avec une ligne entière.
_CRITICAL_PER_LINE = len(CRITICAL_FIELDS & set(LINE_FIELDS))


class Verdict(StrEnum):
    """Ce que vaut une réponse du modèle sur un champ."""

    CORRECT = "correct"
    # Le modèle a dit « je ne sais pas » et l'a envoyé en validation. Coût :
    # du temps humain. C'est le comportement que la décision 5 exige quand il
    # ne sait pas — donc ce n'est pas une faute, c'est un prix à payer.
    ABSTAINED = "abstained"
    # Le modèle a affirmé quelque chose de faux, ou nié un champ qui existe,
    # sans rien envoyer à personne. C'est le seul verdict qui corrompt.
    WRONG = "wrong"


@dataclass(frozen=True)
class Mismatch:
    """Une divergence gardée pour relecture : un taux sans exemples ne se
    diagnostique pas.
    """

    field: str
    expected: str
    got: str
    verdict: Verdict


# Non gelé, contrairement aux autres : c'est un accumulateur qu'on remplit
# pendant le scoring.
@dataclass
class InvoiceScore:
    source_file: str
    quality: SourceQuality
    verdicts: Counter[Verdict] = field(default_factory=Counter)
    missing_lines: list[str] = field(default_factory=list)
    invented_lines: list[str] = field(default_factory=list)
    # Nombre de champs qui partiraient en file de validation humaine. Mesure le
    # COÛT, pas la JUSTESSE : un champ correct mais peu confiant est juste, il
    # prend juste du temps. Les deux se pilotent séparément.
    to_review: int = 0
    # Sous-ensemble des faux qui corrompent un prix. C'est celui-là qui doit
    # tendre vers zéro en premier : un invoice_number faux se rattrape, un
    # unit_price_ht faux part dans price_point, qui est append-only.
    critical_wrong: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def fields_scored(self) -> int:
        return sum(self.verdicts.values())

    @property
    def wrong_rate(self) -> float:
        """Le chiffre à surveiller. Doit tendre vers zéro, pas vers « bien »."""
        return self.verdicts[Verdict.WRONG] / self.fields_scored if self.fields_scored else 0.0


def _normalise(text: str) -> str:
    """Casse et espaces uniquement.

    Pas de suppression d'accents ni de ponctuation : « CREME » et « CRÈME »
    doivent rester distincts, sinon on masque une vraie faiblesse de lecture
    au moment précis où on cherche à la mesurer.
    """
    return " ".join(text.split()).casefold()


def _same_value(expected: object, got: object) -> bool:
    """Égalité adaptée au type. `Decimal("2") == Decimal("2.00")` est vrai."""
    if isinstance(expected, str) and isinstance(got, str):
        return _normalise(expected) == _normalise(got)
    if isinstance(expected, Decimal) and isinstance(got, Decimal):
        return expected == got
    return expected == got


def score_field(expected: object | None, unreadable: bool, got: Extracted) -> Verdict:
    """Applique la matrice vérité × réponse.

    Les neuf cas (valeur / illisible / absent × READ / UNREADABLE / ABSENT) sont
    écrits explicitement plutôt que condensés : c'est le cœur du barème.
    """
    if unreadable:
        # Le champ est sur le papier, mais on n'a pas su le lire.
        if got.status is FieldStatus.UNREADABLE:
            return Verdict.CORRECT  # le modèle non plus, et il le dit
        return Verdict.WRONG  # READ = il a deviné ; ABSENT = il nie et se tait

    if expected is None:
        # Le document ne porte pas ce champ.
        if got.status is FieldStatus.ABSENT:
            return Verdict.CORRECT
        if got.status is FieldStatus.UNREADABLE:
            return Verdict.ABSTAINED  # envoie un humain regarder pour rien
        return Verdict.WRONG  # READ sur un champ inexistant = hallucination pure

    # Le champ est lisible et porte une valeur.
    if got.status is FieldStatus.UNREADABLE:
        return Verdict.ABSTAINED
    if got.status is FieldStatus.ABSENT:
        return Verdict.WRONG  # nier un champ existant : perdu, et sans alerte
    return Verdict.CORRECT if _same_value(expected, got.value) else Verdict.WRONG


def match_lines(
    expected: list[TruthLine], got: list[RawInvoiceLine]
) -> tuple[list[tuple[TruthLine, RawInvoiceLine]], list[TruthLine], list[RawInvoiceLine]]:
    """Apparie sur le libellé normalisé, jamais sur la position.

    En positionnel, une ligne ratée décale tout et une erreur unique en produit
    vingt. Le retrait au fur et à mesure (`pop`) gère les libellés en double,
    courants sur une facture.

    Biais connu : un libellé mal lu compte double, manquant ET inventé. À
    réexaminer sur les vraies factures.
    """
    remaining = list(got)
    pairs: list[tuple[TruthLine, RawInvoiceLine]] = []
    missing: list[TruthLine] = []

    for expected_line in expected:
        target = _normalise(expected_line.raw_label)
        index = next(
            (i for i, line in enumerate(remaining) if _normalise(line.raw_label) == target),
            None,
        )
        if index is None:
            missing.append(expected_line)
        else:
            pairs.append((expected_line, remaining.pop(index)))

    return pairs, missing, remaining


def score_invoice(
    expected: TruthInvoice, got: RawInvoice, review_threshold: float = 0.90
) -> InvoiceScore:
    """Compare une extraction à son annotation, champ par champ."""
    score = InvoiceScore(source_file=expected.source_file, quality=expected.quality)

    def assess(
        prefix: str,
        fields: tuple[str, ...],
        truth: TruthInvoice | TruthLine,
        reading: RawInvoice | RawInvoiceLine,
    ) -> None:
        for name in fields:
            extracted: Extracted = getattr(reading, name)
            verdict = score_field(getattr(truth, name), name in truth.unreadable, extracted)
            score.verdicts[verdict] += 1
            if verdict is Verdict.WRONG and name in CRITICAL_FIELDS:
                score.critical_wrong += 1
            if extracted.needs_review(review_threshold):
                score.to_review += 1
            if verdict is not Verdict.CORRECT:
                score.mismatches.append(
                    Mismatch(
                        field=f"{prefix}{name}",
                        expected=_display(getattr(truth, name), name in truth.unreadable),
                        got=_display(extracted.value, extracted.status is FieldStatus.UNREADABLE),
                        verdict=verdict,
                    )
                )

    assess("", HEADER_FIELDS, expected, got)

    pairs, missing, invented = match_lines(expected.lines, got.lines)
    for i, (truth_line, read_line) in enumerate(pairs):
        assess(f"ligne[{i}].", LINE_FIELDS, truth_line, read_line)

    # Une ligne entièrement ratée fait disparaître tous ses champs sans qu'aucun
    # ne parte en validation : c'est la définition même d'une réponse fausse
    # ici. Les compter à côté du taux le rendait aveugle au pire échec possible
    # — un modèle rendant le seul en-tête affichait un taux flatteur alors qu'il
    # venait de perdre la facture.
    for line in missing:
        score.missing_lines.append(line.raw_label)
        _count_whole_line(score, "ligne manquante", line.raw_label, "<absente>")
    for line in invented:
        score.invented_lines.append(line.raw_label)
        _count_whole_line(score, "ligne inventée", "<inexistante>", line.raw_label)

    return score


def _count_whole_line(score: InvoiceScore, reason: str, expected: str, got: str) -> None:
    """Impute à une ligne ratée le poids des champs qu'elle emporte."""
    score.verdicts[Verdict.WRONG] += len(LINE_FIELDS)
    score.critical_wrong += _CRITICAL_PER_LINE
    score.mismatches.append(
        Mismatch(field=reason, expected=expected, got=got, verdict=Verdict.WRONG)
    )


def _display(value: object | None, unreadable: bool) -> str:
    if unreadable:
        return "<illisible>"
    return "<absent>" if value is None else str(value)


@dataclass(frozen=True)
class CorpusScore:
    """Agrégat sur tout le corpus, ventilé par qualité de source."""

    verdicts: Counter[Verdict]
    by_quality: dict[SourceQuality, Counter[Verdict]]
    missing_lines: int
    invented_lines: int
    to_review: int
    critical_wrong: int
    invoices: list[InvoiceScore]

    @property
    def wrong_rate(self) -> float:
        total = sum(self.verdicts.values())
        return self.verdicts[Verdict.WRONG] / total if total else 0.0

    @property
    def correct_rate(self) -> float:
        total = sum(self.verdicts.values())
        return self.verdicts[Verdict.CORRECT] / total if total else 0.0


def aggregate(scores: list[InvoiceScore]) -> CorpusScore:
    """Somme les scores, ventilés par qualité de source.

    95 % sur des PDF et 40 % sur des photos ne dit pas la même chose qu'un 80 %
    uniforme : un score global masque d'où vient l'échec.
    """
    total: Counter[Verdict] = Counter()
    by_quality: dict[SourceQuality, Counter[Verdict]] = {}

    for score in scores:
        total += score.verdicts
        by_quality.setdefault(score.quality, Counter()).update(score.verdicts)

    return CorpusScore(
        verdicts=total,
        by_quality=by_quality,
        missing_lines=sum(len(s.missing_lines) for s in scores),
        invented_lines=sum(len(s.invented_lines) for s in scores),
        to_review=sum(s.to_review for s in scores),
        critical_wrong=sum(s.critical_wrong for s in scores),
        invoices=scores,
    )
