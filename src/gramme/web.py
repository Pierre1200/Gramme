"""La démo : deux pages, rien de plus (CLAUDE.md, section « Démo »).

- `/rapport` : le rapport du lundi, sur des chiffres fictifs calculés par le
  vrai moteur SQL. Aucune arithmétique ici : les filtres ne font que mettre en
  forme (virgule décimale, signe, pluriel).
- `/envoyer` : reçoit une photo ou un PDF et le range dans `data/raw/` via
  `intake.store()`. La page dit « reçue », jamais « lue » : la lecture
  automatique n'existe pas encore, et rien d'illustratif n'est présenté comme
  fonctionnel.

Aucune authentification : usage local uniquement. Pour la montrer sur un
téléphone, passer par le partage de connexion du téléphone, jamais par le
Wi-Fi d'un établissement.
"""

import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

# `request.form()` renvoie des UploadFile de Starlette. Celui de FastAPI en est
# une sous-classe : un isinstance() avec la mauvaise classe échouerait toujours.
from starlette.datastructures import UploadFile

from gramme.config import get_settings
from gramme.db import Report, read_report
from gramme.intake import EXTENSIONS, RAW_DIR, FilingReport, store

ROOT = Path(__file__).resolve().parents[2]

# Une photo de téléphone pèse 3 à 8 Mo. Au-delà de 20 Mo, ce n'est pas une facture.
MAX_SIZE = 20 * 1024 * 1024

# docs_url=None : pas de page Swagger exposée pendant une démo.
app = FastAPI(title="Gramme — démo", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=ROOT / "templates")


# ---------------------------------------------------------------------------
#  Mise en forme — jamais de calcul
# ---------------------------------------------------------------------------

NBSP = " "
THIN_SPACE = " "  # séparateur de milliers en français
MINUS = "−"  # vrai signe moins, plus lisible que le tiret

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip


def _number(value: Decimal, spec: str) -> str:
    """Applique un format numérique puis les conventions françaises."""
    text = format(value, spec)
    return text.replace(",", THIN_SPACE).replace(".", ",").replace("-", MINUS)


def euros(value: Decimal | None) -> str:
    """Decimal('1234.5') -> « 1 234,50 € »."""
    if value is None:
        return "—"
    return f"{_number(value, ',.2f')}{NBSP}€"


def percent(value: Decimal | None) -> str:
    """Decimal('27.6') -> « 27,6 % »."""
    if value is None:
        return "—"
    return f"{_number(value, '.1f')}{NBSP}%"


def change(value: Decimal) -> str:
    """Decimal('-8.2') -> « −8,2 % », toujours signé."""
    return f"{_number(value, '+.1f')}{NBSP}%"


def drift(value: Decimal) -> str:
    """Écart de ratio : « +1,6 pt », « +2,8 pts ». En français, pluriel à partir de 2."""
    unit = "pt" if abs(value) < 2 else "pts"
    return f"{_number(value, '+.1f')}{NBSP}{unit}"


def long_date(day: date) -> str:
    """date(2026, 9, 15) -> « mardi 15 septembre 2026 »."""
    return f"{DAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def day_month(day: date) -> str:
    """date(2026, 9, 14) -> « 14 septembre »."""
    return f"{day.day} {MONTHS[day.month - 1]}"


def short_date(day: date | None) -> str:
    return "—" if day is None else day.strftime("%d/%m")


for _filter in (euros, percent, change, drift, long_date, day_month, short_date):
    templates.env.filters[_filter.__name__] = _filter


# ---------------------------------------------------------------------------
#  Dépendances — remplacées par des doublures dans tests/test_web.py
# ---------------------------------------------------------------------------


def demo_report() -> Report:
    """Relu à chaque affichage : un `make demo-db` se voit sans redémarrer."""
    return read_report(get_settings().demo_database_url)


def raw_dir() -> Path:
    # Ancré sur la racine du projet plutôt que sur le dossier courant : la page
    # range au bon endroit quel que soit l'endroit d'où uvicorn est lancé.
    return ROOT / RAW_DIR


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/rapport")


@app.get("/rapport", response_class=HTMLResponse)
def report_page(request: Request, report: Annotated[Report, Depends(demo_report)]):
    # « Rapport du lundi — mardi 15 » se contredirait : on affiche la semaine,
    # repérée par son lundi. C'est une date de calendrier, pas un montant.
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return templates.TemplateResponse(
        request,
        "report.html",
        {"page": "report", "report": report, "monday": monday},
    )


@app.get("/envoyer", response_class=HTMLResponse)
def upload_page(request: Request):
    return _render_upload(request, state=None)


@app.post("/envoyer", response_class=HTMLResponse)
async def receive(request: Request, destination: Annotated[Path, Depends(raw_dir)]):
    """Reçoit un document et le range, sans jamais prétendre l'avoir lu.

    Le formulaire est lu à la main plutôt que déclaré en paramètre : un envoi
    sans fichier doit afficher une page lisible, pas une erreur JSON 422.
    """
    # Refus AVANT de lire le corps : sans ce contrôle, Starlette écrit d'abord
    # le fichier entier sur disque, et un envoi de plusieurs gigaoctets remplit
    # le disque avant d'être refusé.
    announced = request.headers.get("content-length")
    if announced is not None and announced.isdigit() and int(announced) > MAX_SIZE:
        return _render_upload(request, state="too_large", status=413)

    form = await request.form()
    upload = form.get("fichier")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return _render_upload(request, state="empty", status=400)

    # Seul le nom de base est conservé : « ../../x.jpg » ne sortira pas du
    # dossier temporaire.
    name = Path(upload.filename).name
    if Path(name).suffix.lower() not in EXTENSIONS:
        return _render_upload(request, state="format", status=400)
    if upload.size is not None and upload.size > MAX_SIZE:
        return _render_upload(request, state="too_large", status=413)

    # Hachage et copie sont des entrées-sorties bloquantes : hors de la boucle
    # asynchrone, pour ne pas figer les autres requêtes pendant ce temps.
    result = await run_in_threadpool(_store_upload, upload.file, name, destination)

    if result.stored:
        return _render_upload(request, state="received", name=result.stored[0].name)
    if result.duplicates:
        return _render_upload(request, state="already_received", name=result.duplicates[0][1].name)
    return _render_upload(request, state="format", status=400)  # extension déjà filtrée


def _store_upload(content: BinaryIO, name: str, destination: Path) -> FilingReport:
    """Écrit l'envoi dans un dossier temporaire, puis le confie à intake.store().

    `store()` travaille sur des chemins : c'est lui, et lui seul, qui décide si
    le document est nouveau ou déjà connu, avec la même règle que le script de
    rangement. La page ne réimplémente rien.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        with path.open("wb") as output:
            shutil.copyfileobj(content, output)
        return store([path], destination)


def _render_upload(request: Request, state: str | None, status: int = 200, name: str | None = None):
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"page": "upload", "state": state, "name": name},
        status_code=status,
    )
