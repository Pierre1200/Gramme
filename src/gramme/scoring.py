"""Le barème : ce que « réussir » veut dire, défini avant d'avoir un résultat.

Écrit avant `extract.py` délibérément (CLAUDE.md, ordre de construction). Une
fois qu'un modèle a produit une sortie, on est tenté de rationaliser — « bon,
la remise sur une ligne séparée, c'est acceptable ». Le barème tranche avant.

Principe central : toutes les erreurs ne coûtent pas la même chose.

    Un montant faux entre dans price_point, qui est append-only : la donnée est
    corrompue pour toujours. Une abstention coûte trente secondes de relecture
    humaine. Les compter ensemble dans une « exactitude » unique masque
    exactement ce qu'on a besoin de savoir.

D'où la règle qui gouverne tout ce module :

    Une réponse qui n'atteint pas l'humain et qui n'est pas juste est un FAUX.

C'est pourquoi répondre ABSENT sur un champ illisible compte comme faux :
`Extracted.needs_review()` renvoie False sur ABSENT, donc rien ne part en
validation et la valeur est perdue en silence.
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
# sont utiles mais rattrapables. On les distingue pour le rapport, sans en faire
# une moyenne pondérée : une moyenne masquerait lequel a cassé.
CHAMPS_CRITIQUES = frozenset({"qty", "unit_price_ht", "line_total_ht", "total_ht", "invoice_date"})


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
    """Une divergence, gardée pour être relue à l'œil.

    Un taux sans exemples n'est pas exploitable : c'est en lisant « attendu
    8.74, obtenu 8.47 » qu'on comprend que le modèle inverse des chiffres.
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

    Trois états attendus (valeur / illisible / absent) contre trois réponses
    possibles (READ / UNREADABLE / ABSENT). Les neuf cas sont explicites
    ci-dessous plutôt que condensés : c'est le cœur du barème, il doit se
    relire sans effort dans six mois.
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

    Comparer la ligne 5 à la ligne 5 est un piège : si le modèle en rate une,
    tout décale et une erreur unique en produit vingt. Le score devient un
    mensonge, et il ment dans le sens pessimiste — on part chasser un bug qui
    n'existe pas.

    Le retrait au fur et à mesure (`pop`) gère les libellés en double, courants
    sur une facture (deux lignes du même article à deux prix).

    Biais connu : un libellé mal lu compte double, en manquante ET en inventée.
    À réexaminer sur les factures de Damien — si ça arrive souvent, il faudra
    un appariement approché.
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

    def evaluer(prefixe: str, champs: tuple[str, ...], source_v, source_o) -> None:
        for champ in champs:
            extrait: Extracted = getattr(source_o, champ)
            verdict = comparer_champ(
                getattr(source_v, champ), champ in source_v.unreadable, extrait
            )
            rapport.verdicts[verdict] += 1
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

    rapport.lignes_manquantes.extend(ligne.raw_label for ligne in manquantes)
    rapport.lignes_inventees.extend(ligne.raw_label for ligne in inventees)
    return rapport


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
    """Somme les rapports.

    La ventilation par qualité n'est pas cosmétique : 95 % sur des PDF et 40 %
    sur des photos, ce n'est pas la même nouvelle qu'un 80 % uniforme. Le
    premier cas dit « le pipeline marche, l'entrée est mauvaise » ; le second
    dit « le pipeline est médiocre partout ».
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
        rapports=rapports,
    )
