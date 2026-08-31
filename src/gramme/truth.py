"""Le jeu de vérité : ce que TU as lu à la main sur le document.

Forme délibérément différente de `RawInvoice` : ni confiance, ni `raw_text`.
On n'annote pas un score, on annote un fait. C'est cette asymétrie qui rend la
mesure honnête — le banc compare une opinion de modèle à une observation
humaine, pas deux sorties de la même machine.

Le format est celui fixé par COLLECTE.md, avec trois ajouts :
  - `unreadable`, sans quoi `None` confond « absent du document » et « présent
    mais illisible » (voir `_AvecIllisibles`) ;
  - `regime`, parce qu'un ticket est en TTC et une facture en HT (voir `Regime`) ;
  - `raw_unit` sur la ligne, information perdue à jamais si on ne l'annote pas.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Emplacements par défaut. `data/` est intégralement gitignoré : rien de ce qui
# est chargé ici ne doit jamais partir sur un remote.
DOSSIER_TRUTH = Path("data/truth")
DOSSIER_RAW = Path("data/raw")


class SourceQuality(StrEnum):
    """Qualité du document source.

    Sert à ventiler les scores du banc. 95 % sur des PDF propres et 40 % sur des
    photos, ce n'est pas du tout la même nouvelle qu'un 80 % uniforme : le
    premier cas dit « le pipeline marche, l'entrée est mauvaise », le second dit
    « le pipeline est médiocre partout ». Sans cette ventilation, un corpus
    déséquilibré donne un chiffre global qui ne veut rien dire.
    """

    PDF = "pdf"  # export propre du fournisseur
    SCAN = "scan"  # scanné à plat, lisible
    PHOTO = "photo"  # téléphone, de biais, froissé
    MANUSCRIT = "manuscrit"  # bon de livraison écrit à la main


class Regime(StrEnum):
    """Les montants du document sont-ils HT ou TTC ?

    Ce n'est pas un détail de présentation. Une facture fournisseur est en HT,
    un ticket de caisse est en TTC, et un auto-entrepreneur en franchise en base
    ne récupère pas la TVA — son coût réel EST le TTC. Annoter un TTC dans un
    champ nommé `line_total_ht` sans le signaler fausserait tous les coûts de
    5,5 à 20 %, silencieusement.

    On annote donc ce qui est imprimé, et ce champ dit de quoi il s'agit. La
    conversion, si elle a lieu, est le travail de `normalize.py` — jamais celui
    de l'annotation.
    """

    HT = "ht"
    TTC = "ttc"


class _AvecIllisibles(BaseModel):
    """Porte la liste des champs illisibles et le garde-fou associé.

    Mutualisé entre la ligne et l'en-tête : la règle est la même aux deux
    niveaux, et deux copies d'une règle finissent toujours par diverger.
    """

    # Champs présents sur le papier mais que TU n'as pas réussi à lire.
    # `None` seul veut dire « le document ne porte pas ce champ » ; nommé ici, il
    # veut dire « il y est, il est illisible ». Sans cette liste, le banc
    # pénalise le modèle qui répond correctement UNREADABLE — c'est-à-dire
    # exactement le comportement que la décision 5 de CLAUDE.md exige de lui.
    unreadable: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _controler_illisibles(self) -> Self:
        """Un champ déclaré illisible ne peut pas porter de valeur.

        Garde-fou d'annotation, pas de logique métier : à trois heures du matin
        sur la quinzième facture, on se trompe. Autant que le fichier refuse de
        se charger plutôt que de fausser une mesure en silence.
        """
        for nom in self.unreadable:
            if nom not in type(self).model_fields:
                raise ValueError(f"unreadable nomme un champ inexistant : {nom}")
            if getattr(self, nom) is not None:
                raise ValueError(f"{nom} est déclaré illisible mais porte une valeur")
        return self


class TruthLine(_AvecIllisibles):
    """Une ligne telle qu'elle est imprimée sur le papier.

    Tous les montants sont optionnels : une ligne peut être partiellement
    illisible sans cesser d'exister. Seul `raw_label` est obligatoire — si tu ne
    peux pas lire le libellé, tu n'as pas une ligne douteuse, tu n'as pas de
    ligne, et elle n'a rien à faire dans le jeu de vérité.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_label: str = Field(min_length=1)
    supplier_ref: str | None = None

    qty: Decimal | None = None
    unit_price_ht: Decimal | None = None
    line_total_ht: Decimal | None = None

    # Unité telle qu'imprimée sur la ligne : "KG", "CT", "PCE", "L"...
    # `invoice_line` n'a pas encore de colonne pour elle (cf. 001_schema.sql,
    # « PIÈGES À TRAITER ENSUITE »), mais l'information est irréversiblement
    # perdue si on ne l'annote pas maintenant.
    raw_unit: str | None = None

    # Colisage, quand la ligne ne le dit pas explicitement : « 10X500G » dans le
    # libellé signifie 5 kg par colis. C'est le piège n° 1 de COLLECTE.md, et il
    # rend tout le prix au kilo faux s'il est raté.
    pack_qty: Decimal | None = None
    base_unit: str | None = None

    notes: str | None = None


class TruthInvoice(_AvecIllisibles):
    """Une facture annotée à la main, dans son intégralité."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Nom du fichier dans data/raw/. Le lien est vérifié au chargement : une
    # annotation orpheline est une annotation qu'on ne peut plus relire.
    source_file: str = Field(min_length=1)
    quality: SourceQuality
    regime: Regime = Regime.HT

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
    pieges: list[str] = Field(default_factory=list)


def load_truth(
    dossier: Path = DOSSIER_TRUTH,
    racine_raw: Path | None = DOSSIER_RAW,
) -> list[TruthInvoice]:
    """Charge et valide tout le jeu de vérité.

    Échoue bruyamment : une annotation invalide contamine toutes les mesures qui
    en dépendent, et vaut mieux être découverte maintenant qu'après trois heures
    d'itération sur un prompt. Le message nomme toujours le fichier fautif —
    sans ça, une erreur Pydantic sur quinze fichiers est inexploitable.

    `racine_raw=None` désactive la vérification du document source, pour les
    tests qui n'ont pas de fichiers sur disque.
    """
    fichiers = sorted(dossier.glob("*.json"))
    if not fichiers:
        raise FileNotFoundError(f"aucune annotation dans {dossier}")

    annotations: list[TruthInvoice] = []
    for fichier in fichiers:
        try:
            annotation = TruthInvoice.model_validate_json(fichier.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{fichier} : annotation invalide\n{e}") from e

        if racine_raw is not None and not (racine_raw / annotation.source_file).exists():
            raise FileNotFoundError(
                f"{fichier} : le document source est introuvable "
                f"({racine_raw / annotation.source_file})"
            )
        annotations.append(annotation)

    return annotations
