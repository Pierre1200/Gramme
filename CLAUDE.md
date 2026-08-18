# Gramme — coût matière pour la restauration et les métiers de bouche

## Contexte

Lecture automatisée de factures fournisseurs pour suivre les prix d'achat,
calculer les coûts matière (parage et rendements compris) et envoyer un
rapport hebdomadaire aux restaurateurs, boulangers et pâtissiers.

Projet solo, en apprentissage. Étudiant développeur, ancien
cuisinier-pâtissier — la connaissance métier est solide, l'expérience
d'architecture Python l'est moins.

**État actuel : phase de collecte de données. Aucun code applicatif écrit.**

## Comment je veux travailler

- **Explique la structure avant d'écrire du code.** Je veux comprendre les
  décisions, pas recevoir un fichier fini.
- **Pas de gros blocs générés d'un coup.** Une fonction, une discussion,
  puis la suivante. Si tu proposes plus de 60 lignes d'un coup, découpe.
- **Utilise le mode plan** pour toute tâche touchant plusieurs fichiers.
- Préviens-moi quand je pars dans une mauvaise direction, même si je
  n'ai rien demandé. Le désaccord argumenté est plus utile que
  l'exécution docile.
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

Vocabulaire métier : le **ratio** est le coût matière rapporté au prix de
vente HT. C'est le chiffre que suivent les restaurateurs.

## Ordre de construction

Strictement séquentiel. Ne propose pas d'étape ultérieure tant que la
précédente n'est pas finie.

1. `models.py` — contrats Pydantic (Invoice, InvoiceLine, confiance par champ)
2. `scripts/evaluate.py` — le banc d'essai, **avant** l'extracteur
3. `llm.py` — client + instrumentation
4. `extract.py` — première extraction, itérer sur le prompt
5. schéma SQL + `db.py`
6. `normalize.py`
7. `report.py`

L'étape 2 avant l'étape 4 est délibérée : définir ce que « réussir » veut
dire avant d'avoir un résultat à défendre.

## Stack

- Python 3.12, `uv` (jamais pip/poetry directement)
- Pydantic v2, psycopg 3, SDK `openai` pointé vers MiniMax
- PostgreSQL 16 via Docker Compose, port 5433
- ruff (lint + format), pytest, pre-commit
- Modèle par défaut : MiniMax-M3, base_url `https://api.minimax.io/v1`

Commandes : `make up`, `make test`, `make lint`, `make eval`

## Interdits

- **Ne jamais commiter quoi que ce soit sous `data/`.** Ce sont des
  factures de vrais clients. Si tu vois un PDF/JPG hors de `data/`,
  signale-le immédiatement.
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
