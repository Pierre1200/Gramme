# Journal de décisions

Ce que le code ne dit pas : pourquoi il est comme ça, et ce qui a été écarté en
chemin. Ordre chronologique, entrée la plus ancienne en premier.

Les décisions d'architecture non négociables vivent dans `CLAUDE.md` ; ce
journal raconte comment on y est arrivé.

---

## 18/08/2026 — Représenter un champ qu'on n'a peut-être pas su lire

**Le problème.** Une facture a des champs qui peuvent être illisibles ou absents.
Comment dire en Pydantic « ce champ vaut 219,00 € avec une confiance de 0,94 »
et « ce champ est illisible, je ne devine pas » ?

**Le vrai enjeu** n'était pas deux états mais **trois** : lu, illisible, absent.
`None` seul ne peut pas les porter, et la distinction n'est pas académique — un
champ illisible part en validation humaine, un champ absent n'y part pas. Sans
cette distinction, la file de validation se remplit de faux positifs et le client
cesse de la regarder.

**Écarté.** Des champs parallèles plats (`qty` + `qty_confidence`) : rien ne
couple les deux, et le troisième état demanderait un champ de plus par valeur.
Une union discriminée à trois classes : c'est le design correct, mais la rigueur
repose entièrement sur le tag choisi par le modèle, et la migration reste
mécanique le jour où l'évaluation la justifiera.

**Retenu.** Une enveloppe générique `Extracted[T]` portant un statut, avec des
invariants imposés par un validateur : impossible de porter une valeur sans
l'assumer, impossible d'annoncer une confiance sur un champ qu'on n'a pas lu.

**Deux conséquences.** La confiance auto-déclarée d'un modèle est mal calibrée :
on lui préfère un signal vérifiable, la **cohérence arithmétique** entre `qty`,
`unit_price_ht` et `line_total_ht` — trois nombres lus séparément sur le papier.
Ce recoupement est un calcul, il appartient donc à Python et à SQL, jamais au
modèle. Et la confiance par champ ne sera **pas persistée** : elle sert à router
vers la validation, le rapport du lundi n'a pas besoin de savoir qu'un champ
était à 0,71 en mars.

## 18/08/2026 — Socle du projet

Dépôt, `pyproject.toml`, Docker Compose, `Makefile`, `.gitignore` (tout `data/`
exclu), pre-commit. Python épinglé en 3.12 : `uv` avait choisi 3.14, en décalage
avec la stack documentée.

## 19/08/2026 — Constituer un jeu de vérité sans clients

Aucune facture professionnelle disponible. Le **ticket de caisse** s'est imposé
comme substitut : même anatomie qu'une facture fournisseur — libellés abrégés,
`qty × prix unitaire`, TVA multi-taux — et disponible immédiatement.

**Limite inscrite dès le départ** : aucun seuil calibré sur ce corpus ne vaudra
en production, les documents personnels arrivant en PDF propre là où les factures
clients arrivent en photo froissée.

Deux pièges notés : le papier thermique s'efface en quelques semaines, et un
ticket est en **TTC** quand une facture fournisseur est en **HT**.

## 23/08/2026 — Le jeu de vérité et le barème

`truth.py` : le format d'annotation, délibérément différent des contrats
d'extraction — ni confiance, ni texte brut. On n'annote pas un score, on annote
un fait.

**Correction apportée au format initial.** `null` ne distinguait pas « absent du
document » de « présent mais illisible ». Sans cette distinction, le banc
pénalise un modèle qui répond correctement « je ne sais pas ». Ajout d'une liste
`unreadable`, et d'un `regime` HT/TTC.

`scoring.py` : le barème, écrit **avant** l'extracteur. Trois verdicts plutôt
qu'une exactitude unique — correct, abstention, faux — parce qu'un montant faux
entre dans un historique immuable tandis qu'une abstention coûte trente secondes
de relecture. Les lignes sont appariées **par libellé**, jamais par position :
en positionnel, une ligne ratée décale tout et une erreur unique en produit
vingt.

**Bug trouvé en écrivant la démonstration.** Les lignes manquantes et inventées
étaient comptées à côté du taux de faux. Un modèle perdant 9 lignes sur 10
affichait 11 % de faux — un chiffre rassurant. Une ligne perdue emporte tous ses
champs sans qu'aucun n'atteigne l'humain : c'est la définition même du faux dans
ce module. Après correction, le même cas affiche 80 %.

## 25/08/2026 — Entrée des documents, et changement de méthode

`intake.py` et `scripts/classer.py` : ranger les documents reçus sans jamais les
altérer, en écartant les doublons par empreinte SHA-256 du contenu. Rien n'est
converti à l'entrée — un HEIC entre en HEIC — parce que transformer l'original
reviendrait à le détruire en croyant rendre service.

**Changement de méthode de travail.** Jusque-là le code était proposé puis
recopié à la main, pour apprendre. La charge de travail en a fait le goulot : à
partir de cette date, le code est écrit directement et relu. Les règles de
l'ancien mode sont conservées, en pause, dans `CLAUDE.md`.

## 15/09/2026 — La découpe de carcasse

Un hôtel-restaurant cobaye achète des carcasses et découpe en interne. Une
carcasse ne donne pas un produit et du déchet : elle donne plusieurs morceaux
vendables et des sous-produits. Le modèle de rendement ne sait pas répartir le
prix d'une carcasse entre ses morceaux.

**Contrainte posée** : la somme des coûts des morceaux doit égaler exactement le
montant facturé ; le déchet n'est pas perdu, son coût est reporté sur les
morceaux utilisés.

**Retenu** : la répartition à la **valeur relative**, chaque morceau recevant un
coefficient. Vérifiée sur un demi-agneau de 10 kg à 12 €/kg : les deux méthodes
retombent exactement sur 120,00 €, mais au poids le carré est compté moitié moins
cher qu'en réalité.

**Ce qui rend la décision peu coûteuse** : la répartition au poids est un cas
particulier de la même formule, tous les coefficients à 1. Le choix de méthode
est donc une **donnée**, pas du code. Rien ne sera modélisé avant d'avoir vu les
premières factures et la méthode de l'employé qui calcule déjà les marges à la
main.

## 15/09/2026 — La démo en deux pages

Un établissement veut voir quelque chose de concret ; une architecture ne se
montre pas.

**Règle tenue** : données fictives, **calcul réel**. Les chiffres sont insérés en
base, et le rapport sort des vraies fonctions SQL. Aucun montant en dur dans une
page. La page d'envoi range réellement le document et ne prétend jamais l'avoir
lu.

**Choix notables.**
- Une **base séparée** `gramme_demo`, recréée de zéro à chaque lancement. Un seed
  qui vide des tables dans la base de production finirait un jour par effacer des
  données clients.
- **Dates du seed relatives au jour courant** : la détection de dérive ne regarde
  que les six dernières semaines, un seed daté en dur afficherait un rapport vide
  un mois plus tard.
- Un **coût à une date donnée** (`recipe_cost_at`) ajouté au schéma : sans lui, la
  colonne « il y a 6 semaines » serait impossible à calculer.
- L'alerte porte sur la **dérive** du ratio, pas sur un seuil absolu : une
  pâtisserie saine est à 22 %, une viande à 35 %.
- **Scénario crédible plutôt qu'alarmiste.** La première version montrait 6 plats
  sur 7 en dérive : un rapport où tout flambe décrédibilise l'outil. Ramené à
  deux alertes, quelques hausses franches et une baisse de saison.

**Trois défauts corrigés en route.** Un utilitaire de test rattachait les prix au
mauvais produit — les tests passaient par coïncidence, jusqu'à ce que les
identifiants se décalent. L'en-tête affichait « Rapport du lundi — mardi 15 ».
Et `make lint` vérifiait le code avant de le formater, s'arrêtant sur des lignes
que le formateur aurait repliées.

## 20/09/2026 — Audit complet

Audit de cohérence, de code et de sécurité : voir [AUDIT.md](AUDIT.md).

**Deux trous de sécurité.** PostgreSQL écoutait sur toutes les interfaces : sur
le partage de connexion d'une démo, la base était atteignable avec les
identifiants de développement. Et le hook pre-commit n'avait jamais été installé
— le garde-fou censé empêcher de commiter une facture était inerte depuis le
premier jour.

**Nettoyage.** Le plafond de taille d'un envoi est désormais vérifié avant de
lire le corps de la requête. `DOSSIER_RAW` a quitté le module d'annotation pour
celui de l'entrée. La tolérance dupliquée entre Python et SQL est verrouillée par
un test. Huit docstrings qui racontaient une histoire ont été ramenées à leur
raison d'être, et le prénom d'un client a été retiré du code.

`setup_projet.md` a été supprimé : il décrivait une arborescence devenue fausse
et faisait doublon avec le README.
