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
RAW_DIR = Path("data/raw")

# Ce qu'une facture peut être. Tout le reste (.DS_Store, .txt, .zip) est ignoré
# en silence plutôt que refusé : on vide un dossier de téléchargement entier.
EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"})

# Accepté et conservé tel quel, mais illisible par poppler et par la plupart
# des modèles : à convertir au moment de l'extraction, jamais à l'entrée.
NEEDS_CONVERSION = frozenset({".heic"})


def fingerprint(path: Path) -> str:
    """SHA-256 du contenu, lu par blocs : un scan 300 dpi pèse des dizaines de Mo."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(65536):
            digest.update(block)
    return digest.hexdigest()


def fingerprints_in(directory: Path) -> dict[str, Path]:
    """Index empreinte → fichier, recalculé à chaque appel.

    Pas d'index sur disque : il se désynchroniserait au premier fichier déplacé
    à la main, et un index faux est pire que pas d'index.
    """
    return {
        fingerprint(item): item
        for item in sorted(directory.iterdir())
        if item.is_file() and item.suffix.lower() in EXTENSIONS
    }


def _free_name(destination: Path, name: str) -> Path:
    """Nom disponible, sans jamais écraser.

    Deux IMG_0001.JPG d'appareils différents ne sont pas des doublons : leurs
    empreintes diffèrent, il faut garder les deux.
    """
    target = destination / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    for index in range(2, 1000):
        target = destination / f"{stem}_{index}{suffix}"
        if not target.exists():
            return target
    raise RuntimeError(f"impossible de trouver un nom libre pour {name}")


@dataclass
class FilingReport:
    stored: list[Path] = field(default_factory=list)
    # (fichier proposé, fichier déjà présent qui a la même empreinte)
    duplicates: list[tuple[Path, Path]] = field(default_factory=list)
    ignored: list[Path] = field(default_factory=list)
    # Rangés, mais dans un format qu'aucun outil ne sait encore lire.
    to_convert: list[Path] = field(default_factory=list)


def store(sources: Iterable[Path], destination: Path) -> FilingReport:
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
    if any(path.resolve().parent == destination.resolve() for path in sources):
        raise ValueError(f"la source est déjà dans {destination}")

    known = fingerprints_in(destination)
    report = FilingReport()

    for source in sorted(sources):
        if not source.is_file():
            continue
        if source.suffix.lower() not in EXTENSIONS:
            report.ignored.append(source)
            continue

        signature = fingerprint(source)
        if signature in known:
            report.duplicates.append((source, known[signature]))
            continue

        target = _free_name(destination, source.name)
        # copy2 préserve les dates du fichier d'origine : c'est souvent le seul
        # indice de quand la photo a été prise.
        shutil.copy2(source, target)

        # L'empreinte est enregistrée immédiatement : deux copies identiques
        # présentes dans le même lot source doivent être vues comme doublons.
        known[signature] = target
        report.stored.append(target)
        if target.suffix.lower() in NEEDS_CONVERSION:
            report.to_convert.append(target)

    return report
