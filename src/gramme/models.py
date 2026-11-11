"""Contrats d'extraction : ce qu'un modèle est autorisé à renvoyer.

Frontière stricte (CLAUDE.md, décision 2) : aucun champ de ce module ne
porte un montant calculé. Tout ce qui est ici a été LU sur un document.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldStatus(StrEnum):
    READ = "read"  # lu sur le document
    UNREADABLE = "unreadable"  # présent mais illisible -> validation humaine
    ABSENT = "absent"  # le document ne porte pas ce champ


class ArithmeticCheck(StrEnum):
    OK = "ok"  # qty × prix unitaire retombe sur le total ligne
    MISMATCH = "mismatch"  # les trois nombres se contredisent
    INCOMPLETE = "incomplete"  # un des trois n'a pas été lu


# Écart maximal toléré entre qty * unit_price_ht et line_total_ht.
# Absolu, pas relatif : les fournisseurs arrondissent au centime par ligne
# (12,5 × 8,47 = 105,875 s'imprime 105,88). Une tolérance relative
# laisserait passer 3 € d'écart sur une ligne à 100 €.
#
# Limite connue, à mesurer avec scripts/evaluate.py : si le fournisseur
# imprime un prix unitaire lui-même arrondi, l'erreur est proportionnelle
# à la quantité (qty=100 → jusqu'à 0,50 €). Un plancher absolu suffira
# tant que les quantités restent petites, pas au-delà.
ARITHMETIC_TOLERANCE = Decimal("0.02")

# Tolérance au niveau facture. Plus lâche : les écarts de ligne s'ajoutent,
# et surtout Σ lignes ≠ total_ht dès qu'il y a une remise de pied de page,
# des frais de port ou une consigne. Un MISMATCH ici est donc une preuve
# plus faible qu'un MISMATCH de ligne — il signale « regarde », pas « faux ».
INVOICE_TOTAL_TOLERANCE = Decimal("0.05")


class Extracted[T](BaseModel):
    """Un champ extrait, avec son état de lecture.

    `value` n'a de sens que si `status is READ`. Les invariants ci-dessous
    rendent impossible de porter une valeur sans l'assumer, ou d'annoncer
    une confiance sur un champ qu'on n'a pas lu.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: FieldStatus
    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Ce que le modèle affirme avoir lu, caractère pour caractère.
    # Sert au recoupement (« 219,00 € » vs Decimal("219.00")) et à la file
    # de validation : un humain corrige plus vite s'il voit la lecture brute.
    raw_text: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.status is FieldStatus.READ:
            if self.value is None:
                raise ValueError("status=read exige une valeur")
            if self.raw_text is None:
                raise ValueError("status=read exige raw_text (traçabilité)")
        else:
            if self.value is not None:
                raise ValueError(f"status={self.status} interdit une valeur")
            if self.confidence != 0.0:
                raise ValueError(f"status={self.status} impose confidence=0")
        return self

    def needs_review(self, threshold: float) -> bool:
        """Vrai si ce champ doit passer par un humain.

        ABSENT ne déclenche rien : un champ que le document ne porte pas
        n'est pas une erreur d'extraction.
        """
        if self.status is FieldStatus.ABSENT:
            return False
        return self.status is FieldStatus.UNREADABLE or self.confidence < threshold


class RawInvoiceLine(BaseModel):
    """Une ligne de facture telle qu'elle a été LUE.

    Couche frontière : sortie brute du modèle, avant normalisation. Rien
    n'est calculé ici, rien n'est converti en unité de base.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Position sur le document. Nu : personne ne corrige un numéro de ligne
    # à la main, et à défaut on retombe sur l'index positionnel.
    line_no: int | None = None

    # Nu et obligatoire. L'incertitude sur un libellé n'est pas une
    # incertitude de lecture mais de MAPPING : elle vit dans
    # supplier_product.status / .confidence, pas ici.
    raw_label: str = Field(min_length=1)

    # Référence article du fournisseur, quand le document en porte une. Nue,
    # comme le libellé : c'est elle qui relie deux libellés d'un même produit
    # quand le fournisseur le renomme.
    supplier_ref: str | None = None

    qty: Extracted[Decimal]

    # L'unité TELLE QU'IMPRIMÉE : "KG", "CT", "PCE", "L", "COLIS"... (un code
    # de norme, « XCT », pour une facture électronique). Non normalisée — la
    # conversion en unité de base est le travail de normalize.py.
    raw_unit: Extracted[str]

    unit_price_ht: Extracted[Decimal]

    # Lu sur le papier, JAMAIS déduit de qty * unit_price_ht. C'est
    # précisément ce qui rend le recoupement arithmétique possible.
    line_total_ht: Extracted[Decimal]

    # Taux de TVA de la ligne, en pourcentage. Optionnel : beaucoup de
    # factures papier ne l'impriment qu'en pied. Il compte pour un client en
    # franchise de TVA, dont le coût réel est le TTC.
    vat_rate: Extracted[Decimal] | None = None

    def check_arithmetic(self, tolerance: Decimal = ARITHMETIC_TOLERANCE) -> ArithmeticCheck:
        """Recoupe qty × unit_price_ht contre le total imprimé sur la ligne.

        Ne lève jamais : une ligne incohérente est un objet valide qu'on
        veut construire, puis router vers la validation. Et ne reconstruit
        rien — déduire le champ manquant rendrait ce contrôle tautologique.
        """
        qty = self.qty.value
        price = self.unit_price_ht.value
        total = self.line_total_ht.value

        # Tester les valeurs plutôt que les statuts : l'invariant de
        # Extracted garantit value is None ⟺ status is not READ, et cette
        # forme-ci écarte le None pour le typage.
        if qty is None or price is None or total is None:
            return ArithmeticCheck.INCOMPLETE

        gap = abs(qty * price - total)
        return ArithmeticCheck.OK if gap <= tolerance else ArithmeticCheck.MISMATCH


class RawAdjustment(BaseModel):
    """Une remise ou des frais de pied de facture, tels qu'ils ont été LUS.

    Ils s'appliquent après le total des lignes : sans eux, une facture à
    remise passerait pour une facture à laquelle il manque une ligne.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1)
    # Signé : négatif pour une remise, positif pour des frais.
    amount_ht: Extracted[Decimal]


class RawInvoice(BaseModel):
    """Une facture telle qu'elle a été LUE.

    C'est exactement le contrat de sortie du modèle : rien ici qui ne se
    lise sur le document. `source_uri` et `source_hash` en sont absents à
    dessein — ils sont produits par le pipeline, et les mettre dans ce
    schéma reviendrait à demander au modèle de les inventer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_name: Extracted[str]
    # SIREN du fournisseur : c'est lui, pas le nom, qui reconnaît un
    # fournisseur déjà connu. Nu comme `raw_label` : sur une facture
    # électronique il est structuré, sur papier on s'en passe.
    supplier_siren: str | None = None
    invoice_number: Extracted[str]
    invoice_date: Extracted[date]
    total_ht: Extracted[Decimal]

    # Hors schéma SQL, et c'est un ajout de ma part : confondre HT et TTC
    # est l'erreur qui coûte le plus cher ici — elle gonfle silencieusement
    # tous les coûts de 5,5 à 20 %. Lire les deux permet de vérifier que
    # total_ttc ≈ total_ht × (1 + taux) et donc qu'on a pris le bon nombre.
    total_ttc: Extracted[Decimal]

    lines: list[RawInvoiceLine] = Field(min_length=1)

    adjustments: list[RawAdjustment] = Field(default_factory=list)

    def check_total(self, tolerance: Decimal = INVOICE_TOTAL_TOLERANCE) -> ArithmeticCheck:
        """Recoupe lignes et pied de facture contre le total HT imprimé.

        C'est le seul contrôle qui attrape une ligne entièrement manquante
        (bas de page coupé au scan) : aucune enveloppe par champ ne peut
        signaler l'absence de ce qui n'a jamais été lu.
        """
        total = self.total_ht.value
        if total is None:
            return ArithmeticCheck.INCOMPLETE

        amounts = [line.line_total_ht.value for line in self.lines]
        amounts += [adjustment.amount_ht.value for adjustment in self.adjustments]
        if any(amount is None for amount in amounts):
            return ArithmeticCheck.INCOMPLETE
        summed = sum(amounts, Decimal(0))

        return ArithmeticCheck.OK if abs(summed - total) <= tolerance else ArithmeticCheck.MISMATCH
