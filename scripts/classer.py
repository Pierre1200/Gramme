"""Range des documents dans data/raw/ en écartant les doublons.

    uv run python scripts/classer.py ~/Downloads/factures_damien

CLI mince : toute la logique est dans `gramme.intake`, pour être testable sans
lancer le script.
"""

import argparse
import sys
from pathlib import Path

from gramme.intake import classer
from gramme.truth import DOSSIER_RAW


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("source", type=Path, help="dossier contenant les documents reçus")
    parseur.add_argument("--vers", type=Path, default=DOSSIER_RAW)
    arguments = parseur.parse_args()

    if not arguments.source.is_dir():
        print(f"dossier introuvable : {arguments.source}", file=sys.stderr)
        return 1

    rapport = classer(arguments.source.iterdir(), arguments.vers)

    print(f"{len(rapport.copies)} document(s) ajouté(s) dans {arguments.vers}")
    for chemin in rapport.copies:
        print(f"  + {chemin.name}")

    if rapport.doublons:
        # Pas une erreur : c'est le comportement attendu quand on renvoie deux
        # fois la même photo. Mais ça doit se voir.
        print(f"\n{len(rapport.doublons)} doublon(s) écarté(s) :")
        for propose, existant in rapport.doublons:
            print(f"  = {propose.name}  (déjà présent sous {existant.name})")

    if rapport.a_convertir:
        print(f"\n{len(rapport.a_convertir)} fichier(s) dans un format non encore lisible :")
        for chemin in rapport.a_convertir:
            print(f"  ! {chemin.name}")
        print("  (conservés tels quels — la conversion se fera à l'extraction)")

    if rapport.ignores:
        print(f"\n{len(rapport.ignores)} fichier(s) ignoré(s), extension non reconnue")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
