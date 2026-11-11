"""Lit les factures électroniques d'un dossier et écrit ce qui a été lu.

    make read                          les vraies factures, dans data/
    make read CORPUS=data/synthetic    le jeu factice

Chaque PDF Factur-X de `raw/` donne un JSON dans `extractions/`, que le banc
d'essai (`make eval`) compare ensuite au jeu de vérité. Les documents qui ne
sont pas des Factur-X sont nommés à part : ils relèvent de la lecture papier.

CLI mince : toute la logique est dans `gramme.facturx`.
"""

import argparse
import sys
from pathlib import Path

from gramme.facturx import NotFacturX, read_invoice


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, default=Path("data"))
    corpus = parser.parse_args().corpus
    raw, extractions = corpus / "raw", corpus / "extractions"

    documents = sorted(raw.glob("*.pdf")) if raw.is_dir() else []
    if not documents:
        print(f"Aucun PDF dans {raw}.")
        return 0

    extractions.mkdir(parents=True, exist_ok=True)
    read, not_facturx, broken = 0, [], []
    for document in documents:
        try:
            invoice = read_invoice(document.read_bytes())
        except NotFacturX:
            not_facturx.append(document.name)
            continue
        except Exception as e:  # XML présent mais refusé : un document cassé
            broken.append(f"{document.name} : {e}")
            continue
        (extractions / f"{document.stem}.json").write_text(
            invoice.model_dump_json(indent=2), encoding="utf-8"
        )
        read += 1

    print(f"{read} facture(s) électronique(s) lue(s) → {extractions}")
    if not_facturx:
        print(f"{len(not_facturx)} document(s) sans XML, à lire autrement :")
        for name in not_facturx:
            print(f"  - {name}")
    if broken:
        print(f"{len(broken)} document(s) illisible(s) :", file=sys.stderr)
        for line in broken:
            print(f"  - {line}", file=sys.stderr)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
