"""Faire entrer des documents dans `data/raw/` sans jamais les altérer.

Décision 4 de CLAUDE.md : le document source est immuable. Ce module copie, il
ne transforme pas ; convertir les HEIC appartient à l'extraction.

Seul traitement : la détection de doublons par empreinte SHA-256 du contenu. Un
même document rangé deux fois gonflerait le score du banc d'essai.
"""

import hashlib
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Où atterrissent les documents reçus. `data/` est intégralement gitignoré.
DOSSIER_RAW = Path("data/raw")

# Ce qu'une facture peut être. Tout le reste (.DS_Store, .txt, .zip) est ignoré
# en silence plutôt que refusé : on vide un dossier de téléchargement entier.
EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"})

# Accepté et conservé tel quel, mais illisible par poppler et par la plupart
# des modèles : à convertir au moment de l'extraction, jamais à l'entrée.
A_CONVERTIR = frozenset({".heic"})


def empreinte(chemin: Path) -> str:
    """SHA-256 du contenu, lu par blocs : un scan 300 dpi pèse des dizaines de Mo."""
    digest = hashlib.sha256()
    with chemin.open("rb") as f:
        while bloc := f.read(65536):
            digest.update(bloc)
    return digest.hexdigest()


def empreintes_du_dossier(dossier: Path) -> dict[str, Path]:
    """Index empreinte → fichier, recalculé à chaque appel.

    Pas d'index sur disque : il se désynchroniserait au premier fichier déplacé
    à la main, et un index faux est pire que pas d'index.
    """
    return {
        empreinte(f): f
        for f in sorted(dossier.iterdir())
        if f.is_file() and f.suffix.lower() in EXTENSIONS
    }


def _nom_libre(destination: Path, nom: str) -> Path:
    """Nom disponible, sans jamais écraser.

    Deux IMG_0001.JPG d'appareils différents ne sont pas des doublons : leurs
    empreintes diffèrent, il faut garder les deux.
    """
    cible = destination / nom
    if not cible.exists():
        return cible
    tige, suffixe = Path(nom).stem, Path(nom).suffix
    for n in range(2, 1000):
        cible = destination / f"{tige}_{n}{suffixe}"
        if not cible.exists():
            return cible
    raise RuntimeError(f"impossible de trouver un nom libre pour {nom}")


@dataclass
class RapportClassement:
    copies: list[Path] = field(default_factory=list)
    # (fichier proposé, fichier déjà présent qui a la même empreinte)
    doublons: list[tuple[Path, Path]] = field(default_factory=list)
    ignores: list[Path] = field(default_factory=list)
    # Copiés, mais dans un format qu'aucun outil ne sait encore lire.
    a_convertir: list[Path] = field(default_factory=list)


def classer(sources: Iterable[Path], destination: Path) -> RapportClassement:
    """Copie dans `destination` les documents encore absents.

    Ne renomme pas selon la convention de COLLECTE.md
    (`2026-08-12_transgourmet_001.pdf`) : la date et le fournisseur sont dans le
    document, et personne ne les connaît avant de l'avoir ouvert. Le renommage
    se fait à l'annotation, quand l'information est sous les yeux.
    """
    destination.mkdir(parents=True, exist_ok=True)

    # Ranger un dossier dans lui-même ferait boucler la copie sur ses propres
    # sorties. Rien dans la signature ne l'interdit, donc on le refuse ici.
    sources = list(sources)
    if any(chemin.resolve().parent == destination.resolve() for chemin in sources):
        raise ValueError(f"la source est déjà dans {destination}")

    connues = empreintes_du_dossier(destination)
    rapport = RapportClassement()

    for source in sorted(sources):
        if not source.is_file():
            continue
        if source.suffix.lower() not in EXTENSIONS:
            rapport.ignores.append(source)
            continue

        signature = empreinte(source)
        if signature in connues:
            rapport.doublons.append((source, connues[signature]))
            continue

        cible = _nom_libre(destination, source.name)
        # copy2 préserve les dates du fichier d'origine : c'est souvent le seul
        # indice de quand la photo a été prise.
        shutil.copy2(source, cible)

        # L'empreinte est enregistrée immédiatement : deux copies identiques
        # présentes dans le même lot source doivent être vues comme doublons.
        connues[signature] = cible
        rapport.copies.append(cible)
        if cible.suffix.lower() in A_CONVERTIR:
            rapport.a_convertir.append(cible)

    return rapport
