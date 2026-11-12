# Gramme

**Suivi du coût matière pour la restauration et les métiers de bouche.**
Les factures fournisseurs sont lues automatiquement ; les coûts de production et
les marges sont recalculés ; un rapport d'une page part chaque lundi.

---

## Le problème

Les prix d'achat changent tous les mois. Les prix à la carte changent une fois
par an. Entre les deux, la marge s'érode sans que personne ne la voie passer.

La suivre demanderait de relire chaque semaine toutes les factures, de comparer
chaque prix à quantité égale, puis de répercuter chaque hausse dans chaque fiche
technique — et dans les sous-recettes de ces fiches. Personne n'a ce temps-là.

Et le prix d'achat n'est pas le coût réel : un bar entier à 22 €/kg revient bien
plus cher une fois levé en filets, un kilo de citrons donne 350 g de jus, une
caisse de fraises perd 10 % à l'équeutage. Tant que le parage et les rendements
ne sont pas dans le calcul, le coût affiché est faux — et il l'est surtout sur
les produits où se joue la marge.

## État d'avancement

Le projet est en construction. Ce qui suit est vrai au 20/09/2026.

| | |
|---|---|
| ✅ Moteur de calcul SQL | coûts, marges, ratios, dérive de prix, sous-recettes récursives |
| ✅ Contrats d'extraction | confiance par champ, trois états de lecture |
| ✅ Banc d'essai | barème et banc écrits **avant** l'extracteur ; `make eval` dit aussi où en est le corpus |
| ✅ Entrée des documents | anti-doublon par empreinte, original jamais altéré |
| ✅ Démo web | deux pages, chiffres fictifs, calcul réel |
| ⬜ Extraction par modèle | prochaine étape |
| ⬜ Normalisation des libellés | libellé fournisseur → ingrédient |
| ⬜ Envoi du rapport | génération et expédition hebdomadaires |

**124 tests**, dont 14 qui s'exécutent dans Postgres, rejoués par l'intégration continue à chaque poussée.

## La démo

Deux pages, pensées pour le téléphone : envoyer une facture en photo, et lire le
rapport du lundi. Les chiffres sont fictifs, mais **calculés par le vrai moteur
SQL** — aucun montant n'est écrit en dur dans une page.

```bash
make demo        # recrée la base de démonstration, puis sert les deux pages
```

La page d'envoi range réellement le document reçu et ne prétend jamais l'avoir
lu : la lecture automatique n'existe pas encore.

## Architecture

Un pipeline, pas un système d'agents : la séquence est connue à l'avance.

```
photo ou PDF  →  intake      rangement, anti-doublon, original intact
              →  extract     lecture par modèle          (à venir)
              →  scoring     mesure contre le jeu de vérité
              →  normalize   libellé fournisseur → ingrédient (à venir)
              →  PostgreSQL  prix, coûts, marges, dérive
              →  report      le rapport du lundi          (à venir)
```

```
db/migrations/     schéma et vues du rapport
db/seed/           données fictives de démonstration
src/gramme/
  models.py        contrats d'extraction (Pydantic)
  truth.py         jeu de vérité annoté à la main
  scoring.py       le barème : ce que « réussir » veut dire
  intake.py        entrée des documents
  bench.py         état du corpus et notation des extractions
  config.py        configuration lue dans .env
  db.py            lecture des vues du rapport
  web.py           les deux pages de la démo
templates/         pages Jinja2 (base, report, upload)
tests/             110 tests
data/              documents clients — intégralement hors Git
```

### Décisions structurantes

- **Le modèle lit, SQL calcule.** Un modèle de langage extrait, normalise et
  rédige ; il ne produit jamais un montant, un pourcentage ou une somme. Tous
  les calculs vivent dans PostgreSQL, y compris pour la démo.
- **Historique de prix immuable.** Aucun `UPDATE` sur un prix ; une règle SQL
  l'interdit physiquement. L'historique *est* le produit.
- **Confiance explicite.** Chaque champ extrait porte un état — lu, illisible,
  absent — et un score. Sous le seuil, le champ part en validation humaine. Un
  modèle qui ne sait pas doit le dire, jamais deviner un montant.
- **Le rendement est porté par le composant, pas par l'ingrédient.** Un citron
  rend 0,35 en jus et 0,03 en zeste : le coût se calcule sur le brut acheté.
- **Le barème avant l'extracteur.** Définir ce que « réussir » veut dire avant
  d'avoir un résultat à défendre.

## Démarrer

```bash
brew install uv                      # une seule fois sur la machine
uv sync                              # environnement et dépendances
uv run pre-commit install            # garde-fous avant chaque commit
cp .env.example .env                 # puis renseigner les variables
make up                              # PostgreSQL, schéma appliqué au 1er lancement
make test
```

| Commande | Effet |
|---|---|
| `make up` / `make down` | démarre / arrête PostgreSQL |
| `make db-reset` | détruit le volume et rejoue les migrations |
| `make demo-db` | recrée la base de démonstration avec des chiffres fictifs |
| `make demo` | base de démonstration, puis les deux pages sur le réseau local |
| `make test` | pytest |
| `make lint` | ruff format puis check |
| `make help` | liste toutes les commandes |
| `make eval` | banc d'essai : extraction comparée au jeu de vérité |

**Stack** — Python 3.12, PostgreSQL 16, Pydantic, psycopg 3, FastAPI, Jinja2,
uv, ruff, pytest. Docker Compose pour la base, GitHub Actions pour la CI.

Les identifiants sont en anglais, les commentaires et la documentation en
français : voir la section « Langue du code » de [CLAUDE.md](CLAUDE.md).

## Confidentialité

Ce sont des factures de vrais établissements : prix négociés, volumes,
fournisseurs. Un restaurateur qui les confie confie sa marge.

- Tout ce qui est sous `data/` est hors Git, sans exception.
- La démo est **locale uniquement**. La page d'envoi n'a pas d'authentification :
  elle se montre depuis le partage de connexion d'un téléphone, jamais depuis le
  Wi-Fi d'un établissement.
- PostgreSQL n'écoute que sur la boucle locale.
- Les identifiants de développement du `docker-compose.yml` ne valent que pour
  une machine de développement.

## Limites connues

Elles sont écrites dans le code, aux endroits concernés, et suivies dans
[AUDIT.md](AUDIT.md) :

- les coproduits (jus et zeste d'un même citron, morceaux d'une même carcasse)
  sont facturés deux fois si on les déclare séparément ;
- l'anti-doublon reconnaît un fichier renvoyé, pas une facture rephotographiée ;
- le prix de vente n'est pas historisé : la dérive mesurée est bien celle des
  coûts.

## Documents

- [JOURNAL.md](JOURNAL.md) — les décisions, dans l'ordre où elles ont été prises
- [AUDIT.md](AUDIT.md) — audit de cohérence, de code et de sécurité
- [COLLECTE.md](COLLECTE.md) — comment le jeu de vérité se constitue
- [CLAUDE.md](CLAUDE.md) — les règles de travail sur ce dépôt

---

Pierre Rouvellat — quinze ans en cuisine et en pâtisserie, aujourd'hui
développeur.
