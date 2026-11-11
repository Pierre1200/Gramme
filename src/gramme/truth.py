"""Le jeu de vérité : ce qu'un humain a lu à la main sur le document.

Forme délibérément différente de `RawInvoice` : ni confiance, ni `raw_text`. On
n'annote pas un score, on annote un fait — c'est cette asymétrie qui rend la
mesure honnête.

Format fixé par COLLECTE.md, avec trois ajouts : `unreadable` (sans quoi `None`
confond « absent » et « illisible »), `vat_basis` (un ticket est en TTC, une
facture en HT) et `raw_unit` sur la ligne.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# RAW_DIR appartient à l'entrée des documents, pas à l'annotation : il est
# réexporté ici pour les appelants qui ne connaissent que ce module.
from gramme.intake import RAW_DIR

TRUTH_DIR = Path("data/truth")

__all__ = [
    "RAW_DIR",
    "TRUTH_DIR",
    "SourceQuality",
    "TruthInvoice",
    "TruthLine",
    "VatBasis",
    "load_truth",
]


class SourceQuality(StrEnum):
    """Qualité du document source, pour ventiler les scores du banc.

    95 % sur des PDF et 40 % sur des photos ne dit pas la même chose qu'un 80 %
    uniforme : un score global masque d'où vient l'échec.
    """

    PDF = "pdf"  # export propre du fournisseur
    SCAN = "scan"  # scanné à plat, lisible
    PHOTO = "photo"  # téléphone, de biais, froissé
    HANDWRITTEN = "handwritten"  # bon de livraison écrit à la main


class VatBasis(StrEnum):
    """Les montants du document sont-ils hors taxes ou toutes taxes comprises ?

    Une facture fournisseur est en HT, un ticket en TTC, et un auto-entrepreneur
    en franchise ne récupère pas la TVA : son coût réel EST le TTC. On annote ce
    qui est imprimé ; la conversion appartient à `normalize.py`.

    Les valeurs restent « ht » et « ttc » : ce sont les mentions portées sur les
    documents eux-mêmes, pas une catégorie que nous aurions inventée.
    """

    EXCLUDING = "ht"
    INCLUDING = "ttc"


class _WithUnreadable(BaseModel):
    """Porte la liste des champs illisibles et le garde-fou associé.

    Mutualisé entre la ligne et l'en-tête : deux copies d'une règle divergent.
    """

    # Champs présents sur le papier mais que l'annotateur n'a pas réussi à lire.
    # `None` seul veut dire « le document ne porte pas ce champ » ; nommé ici, il
    # veut dire « il y est, il est illisible ». Sans cette liste, le banc
    # pénalise le modèle qui répond correctement UNREADABLE — c'est-à-dire
    # exactement le comportement que la décision 5 de CLAUDE.md exige de lui.
    unreadable: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unreadable(self) -> Self:
        """Un champ déclaré illisible ne peut pas porter de valeur.

        Garde-fou d'annotation : mieux vaut un fichier qui refuse de se charger
        qu'une mesure faussée en silence.
        """
        for name in self.unreadable:
            if name not in type(self).model_fields:
                raise ValueError(f"unreadable nomme un champ inexistant : {name}")
            if getattr(self, name) is not None:
                raise ValueError(f"{name} est déclaré illisible mais porte une valeur")
        return self


class TruthLine(_WithUnreadable):
    """Une ligne telle qu'elle est imprimée sur le papier.

    Tous les montants sont optionnels : une ligne peut être partiellement
    illisible sans cesser d'exister. Seul `raw_label` est obligatoire — sans
    libellé lisible il n'y a pas une ligne douteuse, il n'y a pas de ligne.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_label: str = Field(min_length=1)
    supplier_ref: str | None = None

    qty: Decimal | None = None
    unit_price_ht: Decimal | None = None
    line_total_ht: Decimal | None = None

    # Unité telle qu'imprimée sur la ligne : "KG", "CT", "PCE", "L"... Sans
    # elle, « 2 CT » et « 12,5 KG » sont indiscernables, et tout prix au
    # gramme en dépend.
    raw_unit: str | None = None

    # Colisage, quand la ligne ne le dit pas explicitement : « 10X500G » dans le
    # libellé signifie 5 kg par colis. C'est le piège n° 1 de COLLECTE.md, et il
    # rend tout le prix au kilo faux s'il est raté.
    pack_qty: Decimal | None = None
    base_unit: str | None = None

    notes: str | None = None


class TruthInvoice(_WithUnreadable):
    """Une facture annotée à la main, dans son intégralité."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Nom du fichier dans data/raw/. Le lien est vérifié au chargement : une
    # annotation orpheline est une annotation qu'on ne peut plus relire.
    source_file: str = Field(min_length=1)
    quality: SourceQuality
    vat_basis: VatBasis = VatBasis.EXCLUDING

    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None

    total_ht: Decimal | None = None
    # Annoté quand il est imprimé : il permet de vérifier que le HT lu est bien
    # le HT (total_ttc ≈ total_ht × (1 + taux)). Confondre les deux est l'erreur
    # la plus coûteuse du domaine.
    total_ttc: Decimal | None = None

    lines: list[TruthLine] = Field(min_length=1)

    # Ce qui rend cette facture tordue : remise de pied de page, frais de port,
    # consigne, avoir. Sert à expliquer pourquoi Σ lignes ≠ total_ht sans
    # conclure trop vite que l'extraction a raté une ligne.
    pitfalls: list[str] = Field(default_factory=list)


def load_truth(
    directory: Path = TRUTH_DIR,
    raw_root: Path | None = RAW_DIR,
) -> list[TruthInvoice]:
    """Charge et valide tout le jeu de vérité, en nommant le fichier fautif.

    Échoue bruyamment : une annotation invalide contamine toutes les mesures qui
    en dépendent. `raw_root=None` désactive la vérification du document source,
    pour les tests sans fichiers sur disque.
    """
    files = sorted(directory.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"aucune annotation dans {directory}")

    annotations: list[TruthInvoice] = []
    for file in files:
        try:
            annotation = TruthInvoice.model_validate_json(file.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{file} : annotation invalide\n{e}") from e

        if raw_root is not None and not (raw_root / annotation.source_file).exists():
            raise FileNotFoundError(
                f"{file} : le document source est introuvable ({raw_root / annotation.source_file})"
            )
        annotations.append(annotation)

    return annotations
