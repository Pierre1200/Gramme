"""Faire entrer des documents dans `data/raw/` sans jamais les altérer.

Décision 4 de CLAUDE.md : le document source est immuable et tracé. Ce module
copie, il ne transforme pas — pas de recompression, pas de conversion HEIC, pas
de redressement. Un HEIC entre en HEIC. La conversion en image lisible par un
modèle est un problème d'extraction (étape 4), et la faire ici reviendrait à
détruire l'original en croyant rendre service.

Le seul travail effectué est la détection de doublons par empreinte SHA-256 du
contenu. Elle compte : on renvoie toujours deux fois la même photo, et le même
document annoté deux fois gonfle le score du banc et fabrique une fausse série
temporelle.
"""

import hashlib
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Ce qu'une facture peut être. Tout le reste (.DS_Store, .txt, .zip) est ignoré
# en silence plutôt que refusé : on vide un dossier de téléchargement entier.
EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"})

# Les photos d'iPhone arrivent souvent en HEIC. On les accepte et on les garde
# telles quelles, mais rien ne sait encore les lire — ni poppler, ni la plupart
# des modèles. À convertir au moment de l'extraction, pas ici.
EXTENSIONS_A_CONVERTIR_PLUS_TARD = frozenset({".heic", ".webp"})


def empreinte(chemin: Path) -> str:
    """SHA-256 du contenu, lu par blocs.

    Par blocs et pas d'un coup : une facture scannée en 300 dpi pèse plusieurs
    dizaines de mégaoctets, et il n'y a aucune raison de les charger en mémoire.
    """
    digest = hashlib.sha256()
    with chemin.open("rb") as f:
        while bloc := f.read(65536):
            digest.update(bloc)
    return digest.hexdigest()


def empreintes_du_dossier(dossier: Path) -> dict[str, Path]:
    """Index empreinte → fichier, recalculé à chaque fois.

    Pas de fichier d'index sur disque, volontairement : un index se désynchronise
    dès qu'on déplace un fichier à la main, et un index faux est pire que pas
    d'index. Sur quelques centaines de documents, tout relire prend moins d'une
    seconde — on paiera ce prix le jour où il deviendra visible, pas avant.
    """
    return {
        empreinte(f): f
        for f in sorted(dossier.iterdir())
        if f.is_file() and f.suffix.lower() in EXTENSIONS
    }


def _nom_libre(destination: Path, nom: str) -> Path:
    """Trouve un nom disponible sans jamais écraser.

    Deux fichiers différents peuvent porter le même nom (deux téléphones qui
    produisent chacun un IMG_0001.JPG). Ils ont des empreintes différentes,
    donc ce ne sont pas des doublons : il faut les garder tous les deux.
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
        if cible.suffix.lower() in EXTENSIONS_A_CONVERTIR_PLUS_TARD:
            rapport.a_convertir.append(cible)

    return rapport
