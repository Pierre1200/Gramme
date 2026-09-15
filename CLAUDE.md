# Gramme — coût matière pour la restauration et les métiers de bouche

## Contexte

Lecture automatisée de factures fournisseurs pour suivre les prix d'achat,
calculer les coûts matière (parage et rendements compris) et envoyer un
rapport hebdomadaire aux restaurateurs, boulangers et pâtissiers.

Projet solo, en apprentissage. Étudiant développeur, ancien
cuisinier-pâtissier — la connaissance métier est solide, l'expérience
d'architecture Python l'est moins.

**État actuel (15/09/2026) : fondations écrites et testées, extraction pas commencée.**

- Écrit, 76 tests verts : `models.py`, `intake.py`, `truth.py`, `scoring.py`,
  `scripts/classer.py`, schéma SQL (`db/migrations/001_schema.sql`)
- Pas encore écrit : `scripts/evaluate.py` (appelé par `make eval`), `llm.py`,
  `extract.py`, `db.py`, `normalize.py`, `report.py`
- Aucune facture annotée dans `data/truth/`, aucun appel à un modèle
- Premiers documents attendus : un traiteur, et un hôtel-restaurant qui calcule
  déjà ses marges à la main (fiches techniques et factures)
- Ouvert : la méthode de coût des carcasses (voir « Découpe de carcasse »),
  paramètres à confirmer sur les premières factures de l'hôtel-restaurant

## Comment je veux travailler

**Mode actuel : Claude écrit le code, j'oriente et je relis.** J'ai trop de
travail pour tout taper. Ce mode est réversible : quand je dis « on repasse en
mode apprentissage », les règles en pause ci-dessous reprennent effet et
j'écris à nouveau moi-même. Ne pas les supprimer.

Toujours valables :
- **Explique la structure avant d'écrire du code.** Je veux comprendre les
  décisions, pas recevoir un fichier fini.
- **Utilise le mode plan** pour toute tâche touchant plusieurs fichiers.
- Avance par étapes relisibles, et fais le point entre chacune.
- Commente généreusement : le code doit se relire sans toi.
- Préviens-moi quand je pars dans une mauvaise direction, même si je
  n'ai rien demandé. Le désaccord argumenté est plus utile que
  l'exécution docile.

En pause tant que Claude écrit (mode apprentissage) :
- **Pas de gros blocs générés d'un coup.** Une fonction, une discussion,
  puis la suivante. Si tu proposes plus de 60 lignes d'un coup, découpe.
- Propose des indices avant des solutions complètes. Je bloque volontiers
  cinq minutes si ça me fait comprendre.

## Décisions d'architecture — non négociables

Ces choix sont arrêtés. Si tu penses qu'ils sont mauvais, dis-le-moi et
argumente — mais ne les contourne pas silencieusement.

1. **C'est un pipeline, pas un système multi-agents.** La séquence est
   connue à l'avance. Pas de LangGraph, pas de CrewAI, pas d'orchestrateur.
   Des fonctions Python appelées dans l'ordre.

2. **Frontière stricte LLM / SQL.**
   - Le LLM : extrait, normalise, rédige du texte.
   - SQL : tous les calculs (coûts, ratios, écarts, propagation dans les
     sous-recettes).
   - Un LLM ne calcule JAMAIS un montant, un pourcentage ou une somme.
     Aucune exception, quelle que soit la simplicité du calcul.

3. **Historique de prix append-only.** Jamais d'UPDATE sur un prix. Le
   produit vend la détection de dérive : l'historique *est* le produit.

4. **Le document source est immuable et tracé.** Hash anti-doublon, chemin
   conservé, toute donnée extraite pointe vers sa source.

5. **Confiance explicite.** Chaque champ extrait porte un score. Sous le
   seuil, ça part en file de validation humaine. Un modèle qui ne sait pas
   doit dire qu'il ne sait pas — jamais deviner un montant.

6. **Tous les appels LLM passent par `src/gramme/llm.py`.** Une seule porte
   de sortie, avec log systématique : modèle, tokens in/out, coût, durée.
   Si tu vois un appel API ailleurs, signale-le.

7. **Les prompts vivent dans `prompts/`**, pas en chaînes dans le code.

## Modèle de données

Le schéma est déjà conçu (`db/migrations/001_schema.sql`). Points clés :

- Une seule table `recipe` : une sous-recette EST une recette
  (`is_sellable` distingue produit vendu et composant).
- `recipe_component` = arête du graphe, ingrédient XOR sous-recette,
  imposé par un CHECK.
- `yield_ratio` porté par le **composant**, pas par l'ingrédient : un
  citron rend 0,35 en jus et 0,03 en zeste ; un bar entier rend ~0,40 en
  filets. Le coût se calcule sur le brut (`qty / yield_ratio`).
- Coût calculé par CTE récursif avec protection anti-cycle et garde-fou
  de profondeur.
- Tout est stocké en unité de base : g, ml, pièce.
- `recipe.selling_price_ttc` est le prix **d'une portion**, pas du batch :
  `recipe_margin` divise le coût par `portions` mais pas le prix. Saisir 600 €
  pour une paella de 50 au lieu de 12 € donne un ratio de 0,4 %.

Vocabulaire métier : le **ratio** est le coût matière rapporté au prix de
vente HT. C'est le chiffre que suivent les restaurateurs. La **marge brute**
en est le complément, en euros : prix de vente HT − coût matière (ratio 33 %
= marge brute 67 %). Le traiteur raisonne en euros, l'hôtel en ratio : le
rapport doit pouvoir montrer les deux, et les deux se calculent en SQL.

### Trous connus du schéma — migration `002`, avant le seed de la démo

1. **Marge brute en euros** : `recipe_margin` ne renvoie que le ratio. Sans
   colonne SQL, le template ferait la soustraction et violerait la décision 2.
2. **Régime HT/TTC** : n'existe que dans `truth.py`. Un client en franchise de
   TVA ne la récupère pas, son coût réel est le TTC. En V1 (un seul
   établissement), c'est un réglage du client ; le taux, lui, est porté par
   la ligne de facture (5,5 % alimentaire, 20 % le reste).
3. **`selling_price_ttc`** : documenter dans le schéma lui-même que c'est le
   prix d'une portion.

### Découpe de carcasse — méthode retenue, paramètres à confirmer

**Le problème.** L'hôtel-restaurant achète des carcasses aux éleveurs et
découpe en interne. Une carcasse ne donne pas un produit et du déchet : elle
donne plusieurs morceaux vendables (gigot, carré, épaule, collier) et des
sous-produits (os pour les fonds, parures pour le hachis). `yield_ratio`
suppose que tout ce qui n'est pas gardé est perdu, il ne sait pas répartir le
prix d'une carcasse entre ses morceaux.

**Contrainte : toute la carcasse est prise en compte.** La somme des coûts des
morceaux égale exactement le montant facturé. Le déchet réellement jeté n'est
pas perdu : son coût est reporté sur les morceaux utilisés.

**Méthode : répartition à la valeur relative.**

```
coût d'un gramme du morceau i = p × kᵢ / Σⱼ (sⱼ × kⱼ)

p  : prix d'un gramme de carcasse, lu sur la facture de l'éleveur
sᵢ : part du poids de carcasse devenue le morceau i   (Σ sᵢ ≤ 1)
kᵢ : coefficient de valeur relatif du morceau i       (déchet jeté : 0)
```

La répartition au poids en est un cas particulier (tous les kᵢ à 1, déchet à
0). **Le choix de méthode est donc une donnée, pas du code** : les
coefficients de l'employé reproduiront sa méthode, quelle qu'elle soit.

Vérifié sur un demi-agneau de 10 kg à 12 €/kg — les deux méthodes retombent
exactement sur les 120,00 € facturés (coefficients illustratifs) :

| Morceau | kg | k | €/kg à la valeur | €/kg au poids |
|---|---|---|---|---|
| Carré | 1,5 | 2,5 | 24,90 | 12,63 |
| Gigot | 3,0 | 1,6 | 15,93 | 12,63 |
| Épaule | 2,5 | 1,0 | 9,96 | 12,63 |
| Collier, poitrine | 1,5 | 0,6 | 5,98 | 12,63 |
| Os (fonds) | 1,0 | 0,1 | 1,00 | 12,63 |
| Gras jeté | 0,5 | 0 | 0,00 | 0,00 |

Au poids, le carré est compté moitié moins cher qu'en réalité et le collier
plus de deux fois trop cher : la carte se pilote sur des chiffres faux. D'où la
valeur relative par défaut.

**Règles d'implémentation, le moment venu.**
- Calcul en SQL, dans une vue (décision 2). Le coût d'un morceau est **dérivé**
  du `price_point` de la carcasse, jamais inséré comme un `price_point` à part :
  stocker un prix calculé casserait les décisions 3 et 4.
- `ingredient_current_cost` devra lire ces coûts dérivés en plus des prix
  observés : c'est le point d'intégration avec `recipe_cost`.
- Rien n'est modélisé avant les réponses ci-dessous.

**À confirmer sur les premières factures et avec l'employé.**
1. Comment l'éleveur facture : poids carcasse × €/kg, prix à l'animal, au
   quartier ? Poids chaud ou froid ?
2. Comment l'employé répartit aujourd'hui : au poids, à la valeur, pas du tout ?
3. Pèse-t-il les morceaux à chaque découpe, ou utilise-t-il des rendements
   types ? Une pesée par carcasse est de la saisie — incompatible avec « moins
   de deux minutes par semaine », sauf s'il la fait déjà.
4. Que deviennent os, parures et gras : fonds, hachis, poubelle, revente ?
5. D'où viendraient les coefficients : sa propre grille, ou le prix des mêmes
   morceaux achetés prêts chez un grossiste ?

## Ordre de construction

Strictement séquentiel. Ne propose pas d'étape ultérieure tant que la
précédente n'est pas finie. Seule exception : la démo, qui avance `db.py`
(étape 5) — voir « Démo » plus bas.

1. `models.py` — contrats Pydantic (Invoice, InvoiceLine, confiance par champ)
2. `scripts/evaluate.py` — le banc d'essai, **avant** l'extracteur
   (barème écrit : `truth.py`, `scoring.py` ; reste le script lui-même)
3. `llm.py` — client + instrumentation
4. `extract.py` — première extraction, itérer sur le prompt
5. schéma SQL + `db.py` — **avancé pour la démo** : seed et lecture des vues
6. `normalize.py`
7. `report.py`

L'étape 2 avant l'étape 4 est délibérée : définir ce que « réussir » veut
dire avant d'avoir un résultat à défendre.

## Démo : piste parallèle, décidée le 15/09/2026

**Pourquoi.** Un établissement cobaye a validé l'idée mais veut voir quelque
chose de concret. Une architecture ne se montre pas.

**Périmètre : deux pages web, rien de plus.**

1. **Envoyer une facture.** `<input type="file" accept="image/*"
   capture="environment">` : ouvre l'appareil photo sur un téléphone, sans
   appli native. Le fichier passe par `intake.classer()`, il est donc
   réellement reçu et rangé dans `data/raw/`. La page affiche « Facture
   reçue ». **Elle ne prétend jamais que la facture a été lue.**
2. **Le rapport du lundi.** Sur des plats réels de la carte du cobaye, avec
   des données fictives et la mention « exemple, chiffres fictifs » visible.

**Règles de la démo.**

- **Données fictives, calcul réel.** Les chiffres fictifs sont insérés en
  base par un script de seed, et le rapport est produit par les fonctions
  SQL existantes (`recipe_cost`, `recipe_margin`, `price_drift`). Aucun
  montant en dur dans un template : la décision 2 s'applique aussi ici.
- **Dates du seed relatives à `CURRENT_DATE`**, jamais fixes. `price_drift` ne
  regarde que les 42 derniers jours : un seed daté de septembre renvoie une
  section « ce qui a augmenté » vide en novembre, le jour de la démo.
- **Le seed attend la migration `002`** (marge en euros, voir « Trous connus du
  schéma ») : sans elle, la page du rapport devrait calculer.
- Rien d'illustratif n'est présenté comme fonctionnel.
- **Locale uniquement.** Jamais exposée publiquement avec de vraies factures,
  `data/` reste hors Git. Pour la montrer sur téléphone : serveur sur
  `0.0.0.0` sur le **partage de connexion du téléphone**, jamais le Wi-Fi de
  l'établissement : la page d'envoi n'a pas d'authentification, n'importe quel
  client connecté pourrait déposer un fichier dans `data/raw/`.
- **Ne pas surpromettre l'anti-doublon.** L'empreinte SHA-256 reconnaît un
  fichier renvoyé, pas une facture photographiée deux fois : deux photos du
  même papier n'ont pas les mêmes octets.
- Ce n'est pas le début du tableau de bord ni d'une appli mobile : ils
  restent hors périmètre V1.

**Conséquence sur l'ordre de construction.** `db.py` (étape 5) avance pour
servir la démo : seed et lecture des vues. L'extraction (étapes 2 à 4) garde
son ordre et reste gardée par le banc d'essai. Quand de vraies factures
seront annotées, elles remplaceront le seed.

**Structure proposée, à discuter avant d'écrire du code :**

```
src/gramme/db.py        connexion, lecture des vues SQL
src/gramme/web.py       FastAPI : GET/POST /envoyer, GET /rapport
templates/              envoyer.html, rapport.html (Jinja2)
db/seed/demo.sql        données fictives sur les plats du cobaye
```

Le seed vit dans `db/seed/`, **pas** dans `db/migrations/` : tout ce qui est
dans `migrations/` est appliqué automatiquement au premier démarrage de
Postgres. Il s'applique à la main, par exemple via une cible `make seed`.

## Contexte terrain

- **Traiteur** (auto-entrepreneur, un seul fournisseur, surtout de la
  paella) : probablement en franchise de TVA, à confirmer. Si oui, son coût
  réel est le TTC. Un seul fournisseur = bonne série temporelle, mais aucun
  test du rapprochement de libellés entre fournisseurs.
- **Hôtel-restaurant** (~200 couverts, boucherie interne, viande achetée
  directement aux éleveurs) : **un employé calcule déjà les marges à la
  main.** Ses calculs sont la vérité terrain du calcul de coût : comparer le
  rapport à ses chiffres. Il vit l'outil comme une menace. Le traiter en
  expert, lui montrer les résultats avant de les montrer au patron.
  Sa boucherie interne pose le problème de la découpe de carcasse (voir
  « Découpe de carcasse ») : sa méthode de répartition est la spécification.
- **Question ouverte** : comment arrivent leurs factures (mail, facture
  électronique, papier) ? La réponse décide de la place réelle de la photo.

## Stack

- Python 3.12, `uv` (jamais pip/poetry directement)
- Pydantic v2, psycopg 3, SDK `openai` pointé vers MiniMax
- PostgreSQL 16 via Docker Compose, port 5433
- ruff (lint + format), pytest, pre-commit
- Modèle par défaut : MiniMax-M3, base_url `https://api.minimax.io/v1`

Commandes : `make up`, `make test`, `make lint`, `make eval`

Démo : FastAPI, Jinja2, uvicorn, et **python-multipart** (requis par FastAPI
pour recevoir des fichiers). Dépendances validées le 15/09/2026, pas encore
installées.

## Interdits

- **Ne jamais commiter quoi que ce soit sous `data/`.** Ce sont des
  factures de vrais clients. Si tu vois une facture (PDF/JPG) hors de
  `data/`, signale-le immédiatement. Les plaquettes commerciales
  `Gramme_*.pdf|docx` à la racine sont connues et ne sont pas des factures.
- Pas de secrets en dur. Tout passe par `.env`.
- Pas de dépendance ajoutée sans me demander d'abord.
- Ne crée pas de fichiers « au cas où ». Si ça ne sert pas au rapport
  hebdomadaire, ça n'existe pas encore.

## Rappel de cadrage

La V1 fait UNE chose : factures → prix → coûts matière → rapport du lundi.

Hors périmètre pour l'instant : dashboard, gestion de stock, inventaires,
connexion caisse, multi-établissements, appli mobile, authentification
multi-utilisateurs, prévision de production. Si je dérive vers ça,
rappelle-le-moi.

La démo en deux pages (voir plus haut) est une exception bornée et datée,
pas une ouverture du périmètre : la page d'envoi n'est ni une appli mobile
ni l'amorce d'un tableau de bord.
