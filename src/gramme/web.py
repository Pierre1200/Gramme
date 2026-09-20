"""La démo : deux pages, rien de plus (CLAUDE.md, section « Démo »).

- `/rapport` : le rapport du lundi, sur des chiffres fictifs calculés par le
  vrai moteur SQL. Aucune arithmétique ici : les filtres ne font que mettre en
  forme (virgule décimale, signe, pluriel).
- `/envoyer` : reçoit une photo ou un PDF et le range dans `data/raw/` via
  `intake.classer()`. La page dit « reçue », jamais « lue » : la lecture
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
from gramme.db import Rapport, lire_rapport
from gramme.intake import DOSSIER_RAW, EXTENSIONS, RapportClassement, classer

RACINE = Path(__file__).resolve().parents[2]

# Une photo de téléphone pèse 3 à 8 Mo. Au-delà de 20 Mo, ce n'est pas une facture.
TAILLE_MAX = 20 * 1024 * 1024

# docs_url=None : pas de page Swagger exposée pendant une démo.
app = FastAPI(title="Gramme — démo", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=RACINE / "templates")


# ---------------------------------------------------------------------------
#  Mise en forme — jamais de calcul
# ---------------------------------------------------------------------------

ESPACE_INSECABLE = " "
ESPACE_FINE = " "  # séparateur de milliers en français
MOINS = "−"  # vrai signe moins, plus lisible que le tiret

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip


def _nombre(valeur: Decimal, format_: str) -> str:
    """Applique un format numérique puis les conventions françaises."""
    texte = format(valeur, format_)
    return texte.replace(",", ESPACE_FINE).replace(".", ",").replace("-", MOINS)


def euros(valeur: Decimal | None) -> str:
    """Decimal('1234.5') -> « 1 234,50 € »."""
    if valeur is None:
        return "—"
    return f"{_nombre(valeur, ',.2f')}{ESPACE_INSECABLE}€"


def pourcent(valeur: Decimal | None) -> str:
    """Decimal('27.6') -> « 27,6 % »."""
    if valeur is None:
        return "—"
    return f"{_nombre(valeur, '.1f')}{ESPACE_INSECABLE}%"


def variation(valeur: Decimal) -> str:
    """Decimal('-8.2') -> « −8,2 % », toujours signé."""
    return f"{_nombre(valeur, '+.1f')}{ESPACE_INSECABLE}%"


def points(valeur: Decimal) -> str:
    """Écart de ratio : « +1,6 pt », « +2,8 pts ». En français, pluriel à partir de 2."""
    unite = "pt" if abs(valeur) < 2 else "pts"
    return f"{_nombre(valeur, '+.1f')}{ESPACE_INSECABLE}{unite}"


def date_longue(jour: date) -> str:
    """date(2026, 9, 15) -> « mardi 15 septembre 2026 »."""
    return f"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} {jour.year}"


def jour_mois(jour: date) -> str:
    """date(2026, 9, 14) -> « 14 septembre »."""
    return f"{jour.day} {MOIS[jour.month - 1]}"


def date_courte(jour: date | None) -> str:
    return "—" if jour is None else jour.strftime("%d/%m")


for _filtre in (euros, pourcent, variation, points, date_longue, jour_mois, date_courte):
    templates.env.filters[_filtre.__name__] = _filtre


# ---------------------------------------------------------------------------
#  Dépendances — remplacées par des doublures dans tests/test_web.py
# ---------------------------------------------------------------------------


def rapport_de_demo() -> Rapport:
    """Relu à chaque affichage : un `make demo-db` se voit sans redémarrer."""
    return lire_rapport(get_settings().demo_database_url)


def dossier_raw() -> Path:
    # Ancré sur la racine du projet plutôt que sur le dossier courant : la page
    # range au bon endroit quel que soit l'endroit d'où uvicorn est lancé.
    return RACINE / DOSSIER_RAW


# ---------------------------------------------------------------------------
#  Pages
# ---------------------------------------------------------------------------


@app.get("/")
def accueil() -> RedirectResponse:
    return RedirectResponse("/rapport")


@app.get("/rapport", response_class=HTMLResponse)
def page_rapport(request: Request, rapport: Annotated[Rapport, Depends(rapport_de_demo)]):
    # « Rapport du lundi — mardi 15 » se contredirait : on affiche la semaine,
    # repérée par son lundi. C'est une date de calendrier, pas un montant.
    aujourdhui = date.today()
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    return templates.TemplateResponse(
        request,
        "rapport.html",
        {"page": "rapport", "rapport": rapport, "lundi": lundi},
    )


@app.get("/envoyer", response_class=HTMLResponse)
def page_envoyer(request: Request):
    return _page_envoi(request, etat=None)


@app.post("/envoyer", response_class=HTMLResponse)
async def recevoir(request: Request, destination: Annotated[Path, Depends(dossier_raw)]):
    """Reçoit un document et le range, sans jamais prétendre l'avoir lu.

    Le formulaire est lu à la main plutôt que déclaré en paramètre : un envoi
    sans fichier doit afficher une page lisible, pas une erreur JSON 422.
    """
    # Refus AVANT de lire le corps : sans ce contrôle, Starlette écrit d'abord
    # le fichier entier sur disque, et un envoi de plusieurs gigaoctets remplit
    # le disque avant d'être refusé.
    annonce = request.headers.get("content-length")
    if annonce is not None and annonce.isdigit() and int(annonce) > TAILLE_MAX:
        return _page_envoi(request, etat="trop_lourd", statut=413)

    formulaire = await request.form()
    fichier = formulaire.get("fichier")
    if not isinstance(fichier, UploadFile) or not fichier.filename:
        return _page_envoi(request, etat="vide", statut=400)

    # Seul le nom de base est conservé : « ../../x.jpg » ne sortira pas du
    # dossier temporaire.
    nom = Path(fichier.filename).name
    if Path(nom).suffix.lower() not in EXTENSIONS:
        return _page_envoi(request, etat="format", statut=400)
    if fichier.size is not None and fichier.size > TAILLE_MAX:
        return _page_envoi(request, etat="trop_lourd", statut=413)

    # Hachage et copie sont des entrées-sorties bloquantes : hors de la boucle
    # asynchrone, pour ne pas figer les autres requêtes pendant ce temps.
    resultat = await run_in_threadpool(_ranger, fichier.file, nom, destination)

    if resultat.copies:
        return _page_envoi(request, etat="recue", nom=resultat.copies[0].name)
    if resultat.doublons:
        return _page_envoi(request, etat="deja_recue", nom=resultat.doublons[0][1].name)
    return _page_envoi(request, etat="format", statut=400)  # extension déjà filtrée plus haut


def _ranger(contenu: BinaryIO, nom: str, destination: Path) -> RapportClassement:
    """Écrit l'envoi dans un dossier temporaire, puis le confie à intake.classer().

    `classer()` travaille sur des chemins : c'est lui, et lui seul, qui décide
    si le document est nouveau ou déjà connu, avec la même règle que le script
    de classement. La page ne réimplémente rien.
    """
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / nom
        with chemin.open("wb") as sortie:
            shutil.copyfileobj(contenu, sortie)
        return classer([chemin], destination)


def _page_envoi(request: Request, etat: str | None, statut: int = 200, nom: str | None = None):
    return templates.TemplateResponse(
        request,
        "envoyer.html",
        {"page": "envoyer", "etat": etat, "nom": nom},
        status_code=statut,
    )
