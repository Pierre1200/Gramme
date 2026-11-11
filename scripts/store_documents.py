"""Range des documents dans data/raw/ en écartant les doublons.

    uv run python scripts/store_documents.py ~/Downloads/factures_recues

CLI mince : toute la logique est dans `gramme.intake`, pour être testable sans
lancer le script.
"""

import argparse
import sys
from pathlib import Path

from gramme.intake import RAW_DIR, store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="dossier contenant les documents reçus")
    parser.add_argument("--into", type=Path, default=RAW_DIR, help="dossier de destination")
    args = parser.parse_args()

    if not args.source.is_dir():
        print(f"dossier introuvable : {args.source}", file=sys.stderr)
        return 1

    report = store(args.source.iterdir(), args.into)

    print(f"{len(report.stored)} document(s) ajouté(s) dans {args.into}")
    for path in report.stored:
        print(f"  + {path.name}")

    if report.duplicates:
        # Pas une erreur : c'est le comportement attendu quand on renvoie deux
        # fois la même photo. Mais ça doit se voir.
        print(f"\n{len(report.duplicates)} doublon(s) écarté(s) :")
        for offered, existing in report.duplicates:
            print(f"  = {offered.name}  (déjà présent sous {existing.name})")

    if report.to_convert:
        print(f"\n{len(report.to_convert)} fichier(s) dans un format non encore lisible :")
        for path in report.to_convert:
            print(f"  ! {path.name}")
        print("  (conservés tels quels — la conversion se fera à l'extraction)")

    if report.ignored:
        print(f"\n{len(report.ignored)} fichier(s) ignoré(s), extension non reconnue")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
