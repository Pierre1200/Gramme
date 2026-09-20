"""Le barème : ce que « réussir » veut dire, défini avant d'avoir un résultat.

Écrit avant `extract.py` délibérément : une fois la sortie d'un modèle sous les
yeux, on rationalise.

Toutes les erreurs ne coûtent pas la même chose. Un montant faux entre dans
`price_point`, qui est append-only : la donnée est corrompue pour toujours ; une
abstention coûte trente secondes de relecture. D'où la règle du module : une
réponse qui n'atteint pas l'humain et qui n'est pas juste est un FAUX. Répondre
ABSENT sur un champ illisible en est un — `needs_review()` renvoie False, donc
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
CHAMPS_ENTETE = ("supplier_name", "invoice_number", "invoice_date", "total_ht", "total_ttc")
CHAMPS_LIGNE = ("qty", "raw_unit", "unit_price_ht", "line_total_ht")

# Ceux dont une erreur corrompt un prix. Les autres (raw_unit, invoice_number)
# sont utiles mais rattrapables. Comptés à part plutôt que pondérés dans une
# moyenne : une moyenne masquerait lequel a cassé.
CHAMPS_CRITIQUES = frozenset({"qty", "unit_price_ht", "line_total_ht", "total_ht", "invoice_date"})

# Combien de champs critiques disparaissent avec une ligne entière.
_CRITIQUES_PAR_LIGNE = len(CHAMPS_CRITIQUES & set(CHAMPS_LIGNE))


class Verdict(StrEnum):
    """Ce que vaut une réponse du modèle sur un champ."""

    CORRECT = "correct"
    # Le modèle a dit « je ne sais pas » et l'a envoyé en validation. Coût :
    # du temps humain. C'est le comportement que la décision 5 exige quand il
    # ne sait pas — donc ce n'est pas une faute, c'est un prix à payer.
    ABSTENTION = "abstention"
    # Le modèle a affirmé quelque chose de faux, ou nié un champ qui existe,
    # sans rien envoyer à personne. C'est le seul verdict qui corrompt.
    FAUX = "faux"


@dataclass(frozen=True)
class Erreur:
    """Une divergence gardée pour relecture : un taux sans exemples ne se
    diagnostique pas.
    """

    champ: str
    attendu: str
    obtenu: str
    verdict: Verdict


# Non gelé, contrairement aux autres : c'est un accumulateur qu'on remplit
# pendant le scoring.
@dataclass
class RapportFacture:
    source_file: str
    quality: SourceQuality
    verdicts: Counter[Verdict] = field(default_factory=Counter)
    lignes_manquantes: list[str] = field(default_factory=list)
    lignes_inventees: list[str] = field(default_factory=list)
    # Nombre de champs qui partiraient en file de validation humaine. Mesure le
    # COÛT, pas la JUSTESSE : un champ correct mais peu confiant est juste, il
    # prend juste du temps. Les deux se pilotent séparément.
    a_valider: int = 0
    # Sous-ensemble des faux qui corrompent un prix. C'est celui-là qui doit
    # tendre vers zéro en premier : un invoice_number faux se rattrape, un
    # unit_price_ht faux part dans price_point, qui est append-only.
    faux_critiques: int = 0
    erreurs: list[Erreur] = field(default_factory=list)

    @property
    def champs_evalues(self) -> int:
        return sum(self.verdicts.values())

    @property
    def taux_faux(self) -> float:
        """Le chiffre à surveiller. Doit tendre vers zéro, pas vers « bien »."""
        return self.verdicts[Verdict.FAUX] / self.champs_evalues if self.champs_evalues else 0.0


def _normaliser(texte: str) -> str:
    """Casse et espaces uniquement.

    Pas de suppression d'accents ni de ponctuation : « CREME » et « CRÈME »
    doivent rester distincts, sinon on masque une vraie faiblesse de lecture
    au moment précis où on cherche à la mesurer.
    """
    return " ".join(texte.split()).casefold()


def _memes_valeurs(attendu: object, obtenu: object) -> bool:
    """Égalité adaptée au type. `Decimal("2") == Decimal("2.00")` est vrai."""
    if isinstance(attendu, str) and isinstance(obtenu, str):
        return _normaliser(attendu) == _normaliser(obtenu)
    if isinstance(attendu, Decimal) and isinstance(obtenu, Decimal):
        return attendu == obtenu
    return attendu == obtenu


def comparer_champ(attendu: object | None, illisible: bool, obtenu: Extracted) -> Verdict:
    """Applique la matrice vérité × réponse.

    Les neuf cas (valeur / illisible / absent × READ / UNREADABLE / ABSENT) sont
    écrits explicitement plutôt que condensés : c'est le cœur du barème.
    """
    if illisible:
        # Le champ est sur le papier, mais on n'a pas su le lire.
        if obtenu.status is FieldStatus.UNREADABLE:
            return Verdict.CORRECT  # le modèle non plus, et il le dit
        return Verdict.FAUX  # READ = il a deviné ; ABSENT = il nie et se tait

    if attendu is None:
        # Le document ne porte pas ce champ.
        if obtenu.status is FieldStatus.ABSENT:
            return Verdict.CORRECT
        if obtenu.status is FieldStatus.UNREADABLE:
            return Verdict.ABSTENTION  # envoie un humain regarder pour rien
        return Verdict.FAUX  # READ sur un champ inexistant = hallucination pure

    # Le champ est lisible et porte une valeur.
    if obtenu.status is FieldStatus.UNREADABLE:
        return Verdict.ABSTENTION
    if obtenu.status is FieldStatus.ABSENT:
        return Verdict.FAUX  # nier un champ existant : perdu, et sans alerte
    return Verdict.CORRECT if _memes_valeurs(attendu, obtenu.value) else Verdict.FAUX


def apparier_lignes(
    attendues: list[TruthLine], obtenues: list[RawInvoiceLine]
) -> tuple[list[tuple[TruthLine, RawInvoiceLine]], list[TruthLine], list[RawInvoiceLine]]:
    """Apparie sur le libellé normalisé, jamais sur la position.

    En positionnel, une ligne ratée décale tout et une erreur unique en produit
    vingt. Le retrait au fur et à mesure (`pop`) gère les libellés en double,
    courants sur une facture.

    Biais connu : un libellé mal lu compte double, manquant ET inventé. À
    réexaminer sur les vraies factures.
    """
    restantes = list(obtenues)
    paires: list[tuple[TruthLine, RawInvoiceLine]] = []
    manquantes: list[TruthLine] = []

    for ligne_attendue in attendues:
        cible = _normaliser(ligne_attendue.raw_label)
        index = next(
            (i for i, ligne in enumerate(restantes) if _normaliser(ligne.raw_label) == cible),
            None,
        )
        if index is None:
            manquantes.append(ligne_attendue)
        else:
            paires.append((ligne_attendue, restantes.pop(index)))

    return paires, manquantes, restantes


def scorer_facture(
    attendue: TruthInvoice, obtenue: RawInvoice, seuil_validation: float = 0.90
) -> RapportFacture:
    """Compare une extraction à son annotation, champ par champ."""
    rapport = RapportFacture(source_file=attendue.source_file, quality=attendue.quality)

    def evaluer(
        prefixe: str,
        champs: tuple[str, ...],
        source_v: TruthInvoice | TruthLine,
        source_o: RawInvoice | RawInvoiceLine,
    ) -> None:
        for champ in champs:
            extrait: Extracted = getattr(source_o, champ)
            verdict = comparer_champ(
                getattr(source_v, champ), champ in source_v.unreadable, extrait
            )
            rapport.verdicts[verdict] += 1
            if verdict is Verdict.FAUX and champ in CHAMPS_CRITIQUES:
                rapport.faux_critiques += 1
            if extrait.needs_review(seuil_validation):
                rapport.a_valider += 1
            if verdict is not Verdict.CORRECT:
                rapport.erreurs.append(
                    Erreur(
                        champ=f"{prefixe}{champ}",
                        attendu=_afficher(getattr(source_v, champ), champ in source_v.unreadable),
                        obtenu=_afficher(extrait.value, extrait.status is FieldStatus.UNREADABLE),
                        verdict=verdict,
                    )
                )

    evaluer("", CHAMPS_ENTETE, attendue, obtenue)

    paires, manquantes, inventees = apparier_lignes(attendue.lines, obtenue.lines)
    for i, (ligne_v, ligne_o) in enumerate(paires):
        evaluer(f"ligne[{i}].", CHAMPS_LIGNE, ligne_v, ligne_o)

    # Une ligne entièrement ratée fait disparaître tous ses champs sans qu'aucun
    # ne parte en validation : c'est la définition même du FAUX dans ce module.
    # Les compter à côté du taux le rendait aveugle au pire échec possible — un
    # modèle rendant le seul en-tête affichait un taux flatteur alors qu'il
    # venait de perdre la facture.
    for ligne in manquantes:
        rapport.lignes_manquantes.append(ligne.raw_label)
        _compter_ligne_entiere(rapport, "ligne manquante", ligne.raw_label, "<absente>")
    for ligne in inventees:
        rapport.lignes_inventees.append(ligne.raw_label)
        _compter_ligne_entiere(rapport, "ligne inventée", "<inexistante>", ligne.raw_label)

    return rapport


def _compter_ligne_entiere(rapport: RapportFacture, motif: str, attendu: str, obtenu: str) -> None:
    """Impute à une ligne ratée le poids des champs qu'elle emporte."""
    rapport.verdicts[Verdict.FAUX] += len(CHAMPS_LIGNE)
    rapport.faux_critiques += _CRITIQUES_PAR_LIGNE
    rapport.erreurs.append(
        Erreur(champ=motif, attendu=attendu, obtenu=obtenu, verdict=Verdict.FAUX)
    )


def _afficher(valeur: object | None, illisible: bool) -> str:
    if illisible:
        return "<illisible>"
    return "<absent>" if valeur is None else str(valeur)


@dataclass(frozen=True)
class Bilan:
    """Agrégat sur tout le corpus, ventilé par qualité de source."""

    verdicts: Counter[Verdict]
    par_qualite: dict[SourceQuality, Counter[Verdict]]
    lignes_manquantes: int
    lignes_inventees: int
    a_valider: int
    faux_critiques: int
    rapports: list[RapportFacture]

    @property
    def taux_faux(self) -> float:
        total = sum(self.verdicts.values())
        return self.verdicts[Verdict.FAUX] / total if total else 0.0

    @property
    def taux_correct(self) -> float:
        total = sum(self.verdicts.values())
        return self.verdicts[Verdict.CORRECT] / total if total else 0.0


def agreger(rapports: list[RapportFacture]) -> Bilan:
    """Somme les rapports, ventilés par qualité de source.

    95 % sur des PDF et 40 % sur des photos ne dit pas la même chose qu'un 80 %
    uniforme : un score global masque d'où vient l'échec.
    """
    total: Counter[Verdict] = Counter()
    par_qualite: dict[SourceQuality, Counter[Verdict]] = {}

    for rapport in rapports:
        total += rapport.verdicts
        par_qualite.setdefault(rapport.quality, Counter()).update(rapport.verdicts)

    return Bilan(
        verdicts=total,
        par_qualite=par_qualite,
        lignes_manquantes=sum(len(r.lignes_manquantes) for r in rapports),
        lignes_inventees=sum(len(r.lignes_inventees) for r in rapports),
        a_valider=sum(r.a_valider for r in rapports),
        faux_critiques=sum(r.faux_critiques for r in rapports),
        rapports=rapports,
    )
