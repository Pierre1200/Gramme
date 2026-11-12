# Gramme — coût matière pour la restauration et les métiers de bouche

## Contexte

Lecture automatisée de factures fournisseurs pour suivre les prix d'achat,
calculer les coûts matière (parage et rendements compris) et envoyer un
rapport hebdomadaire aux restaurateurs, boulangers et pâtissiers.

Projet solo, en apprentissage. Étudiant développeur, ancien
cuisinier-pâtissier — la connaissance métier est solide, l'expérience
d'architecture Python l'est moins.

**État actuel (11/11/2026) : fondations, démo, jeu de factures factices,
lecteur de factures électroniques et écriture en base écrits et testés ;
normalisation pas commencée.**

- Écrit, 206 tests verts : `models.py`, `intake.py`, `truth.py`, `scoring.py`,
  `bench.py`, `scripts/store_documents.py`, `scripts/evaluate.py`, schéma SQL
  (`001_schema.sql`, `002_rapport.sql`, `003_invoice_detail.sql`,
  `004_missing_price.sql`)
- Jeu factice (`make corpus`) : `corpus/restaurant.toml`, `world.py`,
  `simulation.py`, `facturx.py`, `scripts/generate_corpus.py` — voir « Jeu de
  factures factices » plus bas
- Démo prête (`make demo`) : `config.py`, `db.py` (lecture du rapport), `web.py`,
  `templates/`, seed fictif `db/seed/demo.sql` sur la base `gramme_demo`
- Lecteur de Factur-X (`make read`) : `facturx.read_invoice` rend un
  `RawInvoice` ; un PDF sans XML lève `NotFacturX`. Sur le jeu factice,
  `make eval CORPUS=data/synthetic` donne 438 documents sans un champ faux :
  c'est une vérification de tuyauterie, pas une mesure de qualité
- Écriture en base (`make synthetic-db`) : `db.store_invoice` écrit une
  facture lue, tout ou rien, et dit ce qu'elle est devenue (écrite, déjà
  reçue, numéro en double, à relire). Le jeu factice va dans la base
  `gramme_synthetic` : 438 factures, au centime près du jeu de vérité. Aucun
  `price_point` créé : c'est le travail de la normalisation
- Référentiel du jeu factice : `referential.load_referential` charge les 57
  ingrédients et 38 recettes de `restaurant.toml` dans `gramme_synthetic`
  (`make synthetic-db` le fait avant les factures). Le moteur de coût SQL et
  le simulateur s'accordent sur les 22 produits vendus
- Barème du rapprochement : `models.ProductMapping` (la réponse attendue du
  normalisateur), `truth.MappingTruth`, `scoring.score_mappings`. Chiffre de
  tête : la part des achats HT mal valorisée. La vérité du jeu factice est
  écrite par `make corpus` dans `data/synthetic/mapping/truth.json` ; `make
  eval` l'affiche en section RAPPROCHEMENT
- Pas encore écrit : `normalize.py`, `llm.py`, `report.py`, le tableau de
  bord ; `extract.py` (lecture papier) est reporté
- Aucune vraie facture annotée dans `data/truth/`, aucun appel à un modèle
- Premiers documents attendus : un traiteur, et un hôtel-restaurant qui calcule
  déjà ses marges à la main (fiches techniques et factures)
- Ouvert : la méthode de coût des carcasses (voir « Découpe de carcasse »),
  paramètres à confirmer sur les premières factures de l'hôtel-restaurant
- Documentation : `README.md` (vitrine), `JOURNAL.md` (les décisions et leurs
  raisons), `AUDIT.md` (audit du 20/09/2026), `COLLECTE.md` (jeu de vérité)
- Intégration continue : `.github/workflows/ci.yml`, job `backend` — lint,
  format et tous les tests avec un vrai PostgreSQL

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
- Commente le POURQUOI, pas le récit. Un commentaire dit pourquoi le code est
  ainsi ; il ne raconte pas l'histoire de la décision.
- **Explique chaque terme technique à chaque fois que tu l'emploies**, dans le
  message où il apparaît : sigle développé, puis ce que ça fait concrètement et
  pourquoi on l'utilise. Un mot que je ne sais pas expliquer à l'oral est un mot
  que je ne dois pas rendre.
- **Pas de méta-commentaire dans les livrables.** Contraintes internes, temps
  disponible, niveau : ça ne figure jamais dans un document rendu. On documente
  les décisions et leurs motifs techniques, pas nos circonstances.
- Préviens-moi quand je pars dans une mauvaise direction, même si je
  n'ai rien demandé. Le désaccord argumenté est plus utile que
  l'exécution docile.

En pause tant que Claude écrit (mode apprentissage) :
- **Pas de gros blocs générés d'un coup.** Une fonction, une discussion,
  puis la suivante. Si tu proposes plus de 60 lignes d'un coup, découpe.
- Propose des indices avant des solutions complètes. Je bloque volontiers
  cinq minutes si ça me fait comprendre.

## Méthode Git

- **Une branche par fonctionnalité**, jamais par personne. Nommage :
  `feat/…`, `fix/…`, `chore/…`, `ci/…`, `docs/…`.
- **Les branches partent de `dev` et y retournent** par Pull Request. `dev` est
  la branche d'intégration ; `main` reçoit `dev` quand l'ensemble est stable.
- **`main` et `dev` sont protégées** : Pull Request obligatoire, une
  approbation, et le contrôle `backend` vert.
- **Les Pull Requests sont rédigées en français.** Le code, les identifiants et
  les messages de commit restent en anglais.
- **Messages de commit au format conventionnel, avec portée** :
  `feat(scoring): count missing lines in the wrong rate`.
- **Squash and merge**, puis suppression de la branche. À régler aussi dans les
  réglages GitHub du dépôt : n'autoriser que le squash, sinon le bouton de
  fusion classique reste disponible et la règle écrite ne tient pas.
- **Mettre une branche à jour se fait par rebase**, pas par `merge dev`. Un
  `git merge dev` dans une branche de travail laisse un commit de fusion que le
  squash final n'efface pas, et l'historique cesse d'être linéaire.
- **C'est Pierre qui pousse.** J'écris le code dans les fichiers, mais je ne
  commite ni ne pousse jamais.

## Langue du code

**Tout identifiant est en anglais** : fonctions, classes, variables, constantes,
fichiers, vues et colonnes SQL, classes CSS, noms de tests. Les docstrings, les
commentaires et les textes affichés à l'utilisateur restent **en français**.

Deux exceptions assumées, pour des termes sans équivalent anglais exact : les
valeurs `"ht"` et `"ttc"` (mentions portées sur les documents eux-mêmes) et les
niveaux `ok` / `vigilance` / `alerte` (affichés tels quels).

| Terme métier | Identifiant | Définition |
|---|---|---|
| Facture | `invoice` | Le document reçu d'un fournisseur |
| Ligne de facture | `invoice_line` | Une ligne d'article sur ce document |
| Libellé brut | `raw_label` | Le texte imprimé sur la ligne, avant tout rapprochement |
| Ingrédient | `ingredient` | L'intrant canonique du référentiel, indépendant du fournisseur |
| Rendement, parage | `yield_ratio` | Part utilisable d'un produit brut, portée par le composant |
| Prix relevé | `price_point` | Une observation de prix, jamais modifiée |
| Ratio | `ratio` | Coût matière rapporté au prix de vente HT — terme conservé |
| Marge brute | `margin_per_portion` | Ce qui reste par portion, en euros |
| Dérive | `drift_points` | Écart de ratio en points sur six semaines |
| Jeu de vérité | `truth` | Les annotations faites à la main, référence du banc d'essai |
| Barème | `scoring` | Ce que « réussir » veut dire, défini avant l'extracteur |
| File de validation | `to_review` | Champs sous le seuil de confiance, à relire |
| Empreinte | `fingerprint` | SHA-256 du contenu, base de l'anti-doublon |
| Illisible | `unreadable` | Présent sur le papier, non déchiffré |

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

### Trous connus du schéma

Comblés par `002_rapport.sql` (15/09/2026) : marge brute en euros
(`recipe_margin.marge_brute_ht`, vue `rapport_marges`), commentaire SQL sur
`selling_price_ttc`, coût à une date donnée (`recipe_cost_at`, dont
`recipe_cost` est le cas « aujourd'hui »).

Comblé par `004_missing_price.sql` (12/11/2026) : **un ingrédient sans prix
rend le coût inconnu** (NULL), au lieu de disparaître du total. Le rapport
nomme l'ingrédient sur la carte du plat (« Coût incomplet ») et dans « À
vérifier » ; une dérive dont un bout est inconnu est inconnue (« Pas de
recul »), plus de fausse alerte pour un ingrédient nouveau. L'unique parcours
récursif est `recipe_cost_lines_at`. Verrouillé par
`tests/test_cost.py::TestMissingPrice`.

Restent ouverts :

1. **Régime HT/TTC.** Un client en franchise de TVA ne la récupère pas, son
   coût réel est le TTC. En V1 (un seul établissement), c'est un réglage du
   client. Le taux de chaque ligne est désormais lu et stocké
   (`invoice_line.vat_rate`, depuis `003_invoice_detail.sql`) ; **reste le
   réglage du client et son usage dans le calcul du prix**, à la
   normalisation.
2. **Coproduits.** Jus et zeste d'un même citron, jaunes et blancs d'un même
   œuf : deux composants valorisés chacun sur leur brut font payer deux fois
   le même achat. Même famille de problème que la découpe de carcasse
   ci-dessous. Le seed de démo contourne en ne comptant que le jus.

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

**Révisé le 11/11/2026 : facture électronique d'abord.** Depuis le
1er septembre 2026 les factures arrivent au format Factur-X, dont les données
se lisent sans modèle. La suite se construit sur le jeu factice, dans cet
ordre, sans proposer une étape tant que la précédente n'est pas finie :

1. Le lecteur de Factur-X (XML → `RawInvoice`), dans `facturx.py`. **Fait.**
   Le contrat a gagné `supplier_ref` sur la ligne et `adjustments` (remises et
   frais de pied de facture) sur la facture ; un avoir est rendu en négatif.
2. L'écriture en base des factures lues, dans `db.py`. **Fait.** Un
   fournisseur se reconnaît à son SIREN ; un produit à sa référence, à défaut à
   son libellé ; tout produit naît `pending`, sans prix relevé.
3. `normalize.py` — rapprocher les libellés des ingrédients : c'est là que le
   modèle de langage sert, donc `llm.py` arrive avec lui. Découpée le 12/11 en
   sous-étapes, une PR chacune : le référentiel du jeu factice en base
   (**fait**) ; le barème du rapprochement (**fait**) ; `llm.py` et le premier
   prompt, une fois le fournisseur de modèle choisi ;
   `normalize.py` lui-même. Le trou « ingrédient sans prix ignoré » est
   corrigé (**fait**, 12/11) : un prix manquant rend le coût inconnu
4. Le tableau de bord, alimenté par cette chaîne et non par un seed
5. `report.py`

La lecture papier (étapes 3 et 4 de la liste d'origine ci-dessous) est
reportée, pas abandonnée : les petits fournisseurs ne sont tenus d'émettre en
électronique qu'au 1er septembre 2027. Son code reste en place, sans être
développé davantage.

Liste d'origine, pour mémoire :

1. `models.py` — contrats Pydantic (Invoice, InvoiceLine, confiance par champ)
2. `scripts/evaluate.py` — le banc d'essai, **avant** l'extracteur. **Fait**
   (`truth.py`, `scoring.py`, `bench.py`). `make eval` affiche aussi l'état du
   corpus face aux critères de sortie de COLLECTE.md, utile pendant la collecte.
   Les extractions seront lues dans `data/extractions/`, un JSON par document,
   appariées par `source_file` : le banc se rejoue sans rappeler le modèle.
3. `llm.py` — client + instrumentation
4. `extract.py` — première extraction, itérer sur le prompt
5. schéma SQL + `db.py` — **lecture faite pour la démo** (`002_rapport.sql`,
   `lire_rapport`) ; reste l'écriture des factures extraites
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
2. **Le rapport du lundi.** Sur des plats **génériques** (viande, poisson,
   traiteur, pâtisserie, café), pour montrer la même démo à un restaurant, un
   traiteur ou un pâtissier. Mention « exemple, chiffres fictifs » visible en
   permanence.

**Règles de la démo.**

- **Données fictives, calcul réel.** Les chiffres fictifs sont insérés en
  base par un script de seed, et le rapport est produit par les fonctions
  SQL existantes (`recipe_cost`, `recipe_margin`, `price_drift`). Aucun
  montant en dur dans un template : la décision 2 s'applique aussi ici.
- **Dates du seed relatives à `CURRENT_DATE`**, jamais fixes. `price_drift` ne
  regarde que les 42 derniers jours : un seed daté de septembre renvoie une
  section « ce qui a augmenté » vide en novembre, le jour de la démo.
- **Base séparée `gramme_demo`**, recréée de zéro par `make demo-db`
  (migrations, puis seed). Le seed refuse de s'exécuter sur une autre base : un
  seed qui écrit dans la base principale finirait par écraser des données
  clients.
- **Relancer `make demo` le jour J** : il recrée la base et rafraîchit les dates.
- **Scénario crédible, pas alarmiste** : 2 plats en alerte sur 7, quelques
  hausses franches, une baisse de saison. Un rapport où tout flambe
  décrédibilise l'outil. L'alerte porte sur la dérive du ratio (+2 points en
  6 semaines) ; la tuile d'en-tête ne compte que les alertes.
- **Visuel coloré, esprit appli, pensé d'abord pour le téléphone** : palette
  chaude (crème ; tomate = hausse ; safran = vigilance ; olive = stable),
  onglets en bas, une carte par plat. `web.py` ne calcule rien, ses filtres ne
  font que de la mise en forme (virgule décimale, signe, pluriel).
- **Tester sur un vrai téléphone avant chaque démo** : l'émulation mobile d'un
  navigateur de bureau ne déclenche pas l'appareil photo.
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

**Structure en place :**

```
db/migrations/002_rapport.sql  recipe_cost_at, marge en euros, vues rapport_*
db/seed/demo.sql               chiffres fictifs datés d'aujourd'hui, gramme_demo seule
src/gramme/config.py           adresses des deux bases, lues dans .env
src/gramme/db.py               lire_rapport() : un seul instantané, aucun calcul
src/gramme/web.py              FastAPI : GET /rapport, GET et POST /envoyer
templates/                     base.html, report.html, upload.html
```

Le seed vit dans `db/seed/`, **pas** dans `db/migrations/` : tout ce qui est
dans `migrations/` est appliqué automatiquement au premier démarrage de
Postgres, y compris sur la base principale.

## Jeu de factures factices, décidé le 11/11/2026

**Pourquoi.** Les factures des établissements cobayes n'arrivent pas. Plutôt
que d'attendre, toute la chaîne se construit sur un établissement inventé.

**Ce que c'est.** `corpus/restaurant.toml` décrit « Maison Trébuchet » :
restaurant, traiteur salé, pâtisserie ; 57 ingrédients, 38 recettes, 7
fournisseurs. `simulation.py` en tire les ventes, les commandes, puis environ
440 factures sur douze mois ; `make corpus` les écrit en PDF Factur-X dans
`data/synthetic/`, chacune avec son annotation de vérité.

**Règles.**

- **Ce que le jeu prouve, et ce qu'il ne prouve pas.** Il prouve que la chaîne
  fonctionne de bout en bout, avec une vérité exacte. Il ne prouve ni que
  Gramme lit de vraies factures, ni que quelqu'un paiera. Ne jamais présenter
  un score obtenu dessus comme une mesure de qualité.
- **Jamais mélangé aux vraies factures.** Le factice vit dans
  `data/synthetic/`, les vraies dans `data/raw/` : `make eval
  CORPUS=data/synthetic` choisit l'un ou l'autre, jamais les deux.
- **Fictif par construction.** Aucune enseigne réelle ; les SIREN échouent au
  contrôle de Luhn (`world.py` refuse un SIREN valide) ; téléphones dans la
  tranche réservée à la fiction ; mention « Document fictif » sur chaque PDF
  et dans chaque XML.
- **Prix réels, trajectoires inventées.** Le prix du fichier est un prix relevé
  en octobre-novembre 2026 (cotations RNM de FranceAgriMer, catalogues Metro),
  appliqué la dernière semaine ; chaque produit nomme sa source (`src`), et
  `estimate` signale ceux qui n'en ont pas. Les hausses qui y mènent sont un
  scénario.
- **Dates relatives à aujourd'hui**, comme le seed de démo : relancer `make
  corpus` avant une démonstration.
- **Le simulateur calcule des montants en Python** : il joue le logiciel de
  facturation des fournisseurs, extérieur au produit. La décision 2 vaut pour
  Gramme, dont les calculs restent en SQL.
- `make corpus` demande Chrome sur la machine ; les tests, non.

**Limites connues.** Le PDF imprimé par Chrome n'est pas un PDF/A-3 certifié.
Le XML est contrôlé contre le schéma officiel, pas contre les règles de gestion
de la norme (elles demandent un serveur de validation) : nos tests vérifient
sommes et TVA. La bibliothèque n'émet pas le profil BASIC : tout est au profil
EN 16931.

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
- `factur-x` (Akretion, BSD) pour écrire et relire les factures électroniques,
  validée le 11/11/2026 ; tout passe par `src/gramme/facturx.py`
- PostgreSQL 16 via Docker Compose, port 5433
- ruff (lint + format), pytest, pre-commit
- Modèle par défaut : MiniMax-M3, base_url `https://api.minimax.io/v1`

Commandes : `make help` les liste toutes.

Démo : FastAPI, Jinja2, uvicorn, et **python-multipart** (requis par FastAPI
pour recevoir des fichiers). Validées et installées le 15/09/2026.

## Interdits

- **Ne jamais commiter quoi que ce soit sous `data/`.** Ce sont des
  factures de vrais clients. Si tu vois une facture (PDF/JPG) hors de
  `data/`, signale-le immédiatement. Les plaquettes commerciales
  `Gramme_*.pdf|docx` à la racine sont connues et ne sont pas des factures.
- Pas de secrets en dur. Tout passe par `.env`.
- Le hook pre-commit doit rester installé (`uv run pre-commit install`) : c'est
  lui qui refuse les gros fichiers et les clés privées avant un commit. Il est
  resté absent pendant un mois sans que personne ne le remarque.
- Pas de dépendance ajoutée sans me demander d'abord.
- Ne crée pas de fichiers « au cas où ». Si ça ne sert pas au rapport
  hebdomadaire, ça n'existe pas encore.

## Avant de rendre le dépôt public

Le dépôt est privé aujourd'hui. Ce qu'il faut retirer ou remplacer avant toute
publication est listé dans `AUDIT.md`, section « Avant de rendre le dépôt
public » : données de prospection de `COLLECTE.md`, contexte commercial des deux
établissements cobayes, identifiants de développement du `docker-compose.yml`.

## Rappel de cadrage

La V1 fait UNE chose : factures → prix → coûts matière → rapport du lundi.

**Le tableau de bord est entré dans le périmètre le 11/11/2026** : il vient
après la chaîne de lecture (voir « Ordre de construction »), jamais avant.

Hors périmètre pour l'instant : gestion de stock, inventaires, connexion
caisse, multi-établissements, appli mobile, authentification
multi-utilisateurs, prévision de production. Si je dérive vers ça,
rappelle-le-moi.

**Avant toute mise en ligne sur un serveur** : la page d'envoi n'a aucune
authentification, elle ne s'expose pas telle quelle ; et seul le jeu factice a
sa place sur une machine publique.

La démo en deux pages (voir plus haut) est une exception bornée et datée,
pas une ouverture du périmètre : la page d'envoi n'est ni une appli mobile
ni l'amorce d'un tableau de bord.
