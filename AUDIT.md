# Audit — 20/09/2026

Audit de cohérence, de code et de sécurité, mené avant la reprise du
développement de l'extraction. Portée : tout le dépôt, y compris l'historique
Git et la configuration d'infrastructure.

**Méthode.** Relecture intégrale des sources, des migrations et des templates ;
recherche de secrets et de données clients dans l'historique Git complet ;
inspection de l'exposition réseau ; inventaire des dépendances déclarées contre
les dépendances réellement importées ; exécution de la suite de tests et du
linter.

**Résultat.** Deux défauts de sécurité de gravité élevée, corrigés et vérifiés.
Aucune fuite de donnée client. `110 tests` au vert, linter propre.

---

## Ce qui était déjà sain

- `.env` n'a jamais été commité.
- **Aucun fichier sous `data/` dans tout l'historique Git** — vérifié sur
  l'ensemble des branches et des commits.
- Aucune clé ni mot de passe en dur dans le code.
- Toutes les requêtes SQL sont paramétrées : pas de concaténation de valeurs.
- La traversée de chemin à l'envoi est traitée et couverte par un test
  (`../../evasion.jpg` atterrit bien dans le dossier de destination).
- L'échappement HTML de Jinja est actif ; la documentation d'API est désactivée.

## Sécurité

| # | Constat | Gravité | Statut |
|---|---|---|---|
| S1 | PostgreSQL était publié sur **toutes** les interfaces. Pendant une démo sur un partage de connexion, la base était atteignable avec les identifiants de développement. | Élevée | **Corrigé** — lié à `127.0.0.1`, refus depuis l'adresse réseau vérifié |
| S2 | Le hook **pre-commit n'était pas installé**. `detect-private-key` et le refus des gros fichiers ne tournaient jamais : le garde-fou contre un commit de facture était inerte. | Élevée | **Corrigé** — hook installé, étape ajoutée au README |
| S3 | Le plafond de 20 Mo était vérifié **après** lecture du corps de la requête, donc après écriture du fichier sur disque. | Moyenne | **Corrigé** — refus sur l'en-tête `content-length`, avec test |
| S4 | Ni authentification ni protection CSRF sur l'envoi de facture. | Faible | **Accepté** — usage local, documenté dans le README |
| S5 | Le type de fichier n'est vérifié que par l'extension, jamais par le contenu. | Faible | **Accepté** — sans effet tant que rien n'ouvre le fichier ; à revoir quand l'extraction le lira |
| S6 | Identifiants de développement en clair dans `docker-compose.yml`. | Faible | **Accepté** — documenté, à ne pas reproduire au déploiement |

## Cohérence

| # | Constat | Statut |
|---|---|---|
| C1 | `DOSSIER_RAW` était défini dans le module d'annotation alors que c'est une notion d'entrée ; deux appelants importaient un chemin d'entrée depuis `truth.py`. | **Corrigé** — déplacé dans `intake.py`, réexporté pour les appelants existants |
| C2 | La tolérance de 0,05 € existait en double, en Python et en dur dans une vue SQL, sans rien pour empêcher la dérive. | **Corrigé** — un test compare les deux valeurs |
| C3 | Deux styles de modèles coexistent : Pydantic pour les contrats d'extraction, dataclasses pour les lignes lues en base. | **Accepté** — le choix est justifié (validation d'entrée contre transport de données), désormais expliqué dans le README |
| C4 | `setup_projet.md` décrivait une arborescence fausse et doublait le README. | **Corrigé** — supprimé, conservé dans l'historique Git |
| C5 | Le README ne mentionnait ni la démo, ni les modules écrits depuis un mois. | **Corrigé** — réécrit |

## Code

| # | Constat | Statut |
|---|---|---|
| K1 | Huit docstrings racontaient une histoire au lieu de dire leur raison d'être, dont un en-tête de module de 21 lignes. | **Corrigé** — ramenées à 1 à 3 lignes, le « pourquoi » conservé |
| K2 | Le prénom d'un client apparaissait dans trois fichiers. | **Corrigé** |
| K4 | `EXTENSIONS_A_CONVERTIR_PLUS_TARD` : nom à rallonge. | **Corrigé** — `A_CONVERTIR` |
| K6 | La connexion à la base n'avait pas de délai d'attente : une base éteinte figeait la page au lieu d'afficher une erreur. | **Corrigé** — délai de 5 s et exception dédiée `BaseInjoignable` |
| K7 | `make test` affichait « 94 passed, 14 skipped » sans dire pourquoi : on pouvait croire à un succès complet. | **Corrigé** — les raisons des tests ignorés s'affichent |

## Dépendances

| # | Constat | Statut |
|---|---|---|
| D1 | `openai` est déclaré mais pas encore utilisé. | **Accepté** — prévu pour l'étape d'extraction |
| D2 | `httpx` n'est utilisé que par le client de test. | **Corrigé** — déplacé dans les dépendances de développement |

## Limites fonctionnelles connues

Ce ne sont pas des défauts d'implémentation mais des choix de modélisation
assumés, chacun documenté à l'endroit concerné et verrouillé par un test quand
c'est possible.

1. **Un ingrédient sans prix disparaît du calcul en silence** et le coût est
   sous-estimé. Verrouillé par un test. À corriger avant la saisie des vraies
   fiches techniques : c'est là qu'apparaîtront des ingrédients jamais facturés.
2. **Les coproduits sont facturés deux fois** si on les déclare séparément : jus
   et zeste d'un même citron, morceaux d'une même carcasse. Même famille de
   problème que la découpe, traitée dans `CLAUDE.md`.
3. **Le prix de vente n'est pas historisé** : la dérive mesurée est bien celle
   des coûts, pas un changement de carte.
4. **L'anti-doublon reconnaît un fichier renvoyé**, pas une facture
   rephotographiée : deux photos du même papier n'ont pas les mêmes octets.

## Avant de rendre le dépôt public

Le dépôt est privé aujourd'hui. Ces points sont à traiter avant toute
publication :

- [ ] `COLLECTE.md` contient des **données de prospection** : noms
      d'établissements réels et relations personnelles.
- [ ] `CLAUDE.md` décrit le contexte commercial de deux établissements cobayes.
- [ ] Les identifiants de développement du `docker-compose.yml` doivent être
      remplacés par des variables d'environnement pour tout déploiement.
- [ ] Vérifier une dernière fois qu'aucun document n'a été ajouté hors `data/`.

## Vérifications passées

```
make test     110 tests, aucun échec
make lint     aucune erreur
docker compose port db 5432     127.0.0.1:5433
connexion PostgreSQL depuis l'adresse réseau     refusée
git log --all -- 'data/*'                        aucun résultat
```
