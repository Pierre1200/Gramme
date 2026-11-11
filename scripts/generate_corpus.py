"""Fabrique le jeu de factures factices, au format Factur-X.

    make corpus

Pour chaque facture simulée : le gabarit HTML est imprimé en PDF par Chrome,
le XML de la norme y est joint, et l'annotation de vérité est écrite à côté.

    data/synthetic/raw/     les factures, un PDF Factur-X par document
    data/synthetic/truth/   leur vérité, un JSON par document
    data/synthetic/resent/  les factures renvoyées une seconde fois, à l'identique

Les dates partent d'aujourd'hui : relancer la commande rafraîchit tout le jeu.
Demande Chrome ou Chromium sur la machine (variable CHROME_PATH pour un autre
emplacement).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from gramme import facturx
from gramme.simulation import CORPUS_MARKER, Invoice, simulate, to_truth
from gramme.world import World, load_world

TEMPLATE_DIR = Path("corpus")
OUTPUT_DIR = Path("data/synthetic")
WORKERS = 6
CHROME_TIMEOUT = 30  # secondes ; une facture s'imprime en une seconde
CHROME_ATTEMPTS = 3

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> str:
    for candidate in (os.environ.get("CHROME_PATH"), *CHROME_CANDIDATES):
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise SystemExit("Chrome introuvable : installer Chrome ou renseigner CHROME_PATH.")


def french(value: Decimal, pattern: str) -> str:
    """Écriture française d'un nombre : espace fine des milliers, virgule décimale."""
    return format(value, pattern).replace(",", " ").replace(".", ",")


def build_environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True, undefined=StrictUndefined
    )
    environment.filters["money"] = lambda value: french(value, ",.2f")
    environment.filters["rate"] = lambda value: french(value.normalize(), "f") + " %"
    environment.filters["qty"] = lambda value: french(
        value, "f" if value == value.to_integral_value() else ".3f"
    )
    return environment


def render_html(environment: Environment, world: World, invoice: Invoice) -> str:
    supplier = world.suppliers[invoice.supplier]
    return environment.get_template("invoice.html.j2").render(
        invoice=invoice,
        supplier=supplier,
        buyer=world.establishment,
        title="Avoir" if invoice.kind == "credit_note" else "Facture",
        discounts=[a for a in invoice.adjustments if a.amount < 0],
        charges=[a for a in invoice.adjustments if a.amount >= 0],
        vat_number=facturx.invoice_data(world, invoice)["BG-4"]["vat_identifier"],
        notice=facturx.NOTICE,
    )


def print_to_pdf(chrome: str, html: str, workdir: Path) -> bytes:
    """Imprime une page HTML en PDF avec Chrome, sans l'afficher."""
    # Un profil par fil d'exécution : deux Chrome qui partagent un profil se
    # bloquent l'un l'autre.
    name = f"worker-{threading.get_ident()}"
    page, pdf = workdir / f"{name}.html", workdir / f"{name}.pdf"
    page.write_text(html, encoding="utf-8")
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--user-data-dir={workdir / name}",
        f"--print-to-pdf={pdf}",
        page.as_uri(),
    ]
    # Il arrive qu'un Chrome sans affichage ne rende jamais la main : mieux vaut
    # le relancer que perdre tout le jeu à cause d'une seule facture.
    for attempt in range(1, CHROME_ATTEMPTS + 1):
        pdf.unlink(missing_ok=True)
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=CHROME_TIMEOUT)
            return pdf.read_bytes()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            if attempt == CHROME_ATTEMPTS:
                raise
    raise AssertionError("inatteignable")


def prepare_output(output: Path) -> None:
    """Vide le dossier du jeu précédent, et seulement celui-là."""
    # Seul un dossier portant le marqueur a été créé par ce script : rien
    # d'autre n'est jamais vidé.
    if output.exists() and any(output.iterdir()) and not (output / CORPUS_MARKER).exists():
        raise SystemExit(f"{output} n'a pas été créé par ce script : je ne le vide pas.")
    for name in ("raw", "truth", "resent"):
        shutil.rmtree(output / name, ignore_errors=True)
        (output / name).mkdir(parents=True)
    (output / CORPUS_MARKER).touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--limit", type=int, help="ne produire que les N dernières factures")
    args = parser.parse_args()

    chrome = find_chrome()
    world = load_world()
    corpus = simulate(world, args.until)
    invoices = corpus.invoices[-args.limit :] if args.limit else corpus.invoices
    environment = build_environment()
    prepare_output(args.out)

    def produce(invoice: Invoice) -> None:
        supplier = world.suppliers[invoice.supplier]
        pdf = print_to_pdf(chrome, render_html(environment, world, invoice), workdir)
        document = facturx.embed(
            pdf, facturx.build_xml(world, invoice), title=invoice.number, author=supplier.legal
        )
        source_file = f"{invoice.number}.pdf"
        (args.out / "raw" / source_file).write_bytes(document)
        truth = to_truth(invoice, source_file, unit_of=facturx.unit_code)
        (args.out / "truth" / f"{invoice.number}.json").write_text(
            truth.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )

    with tempfile.TemporaryDirectory() as temporary:
        workdir = Path(temporary)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for done, _ in enumerate(pool.map(produce, invoices), start=1):
                if done % 50 == 0 or done == len(invoices):
                    print(f"  {done}/{len(invoices)} factures", file=sys.stderr)

    produced = {invoice.number for invoice in invoices}
    resent = [number for number in corpus.resent if number in produced]
    for number in resent:
        shutil.copyfile(
            args.out / "raw" / f"{number}.pdf", args.out / "resent" / f"{number}-renvoi.pdf"
        )

    print(
        f"{len(invoices)} factures Factur-X dans {args.out}/raw, "
        f"du {invoices[0].issued_on:%d/%m/%Y} au {invoices[-1].issued_on:%d/%m/%Y} ; "
        f"{len(resent)} renvoyée(s) en double."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
