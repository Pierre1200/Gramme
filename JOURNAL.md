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

## 27/10/2026 — Conventions de code, méthode Git, intégration continue

Le dépôt vivait encore en direct sur `main`, sans intégration continue, avec des
identifiants français. Trois chantiers menés ensemble, parce qu'ils se tiennent.

**Identifiants en anglais, commentaires en français.** Le moment était le bon :
3 500 lignes et 110 tests comme filet. Après l'extraction, le même chantier
aurait coûté le double, sur du code neuf encore non couvert.

Deux exceptions assumées, pour des termes sans équivalent anglais exact : les
valeurs `"ht"` et `"ttc"`, qui sont les mentions portées sur les documents
eux-mêmes, et les niveaux `ok` / `vigilance` / `alerte`, affichés tels quels.
`"manuscrit"` est devenu `"handwritten"` : celui-là est notre propre catégorie,
pas une mention imprimée. Le format d'annotation pouvait changer sans douleur —
`data/truth/` est encore vide.

**Ce que le renommage a cassé, et que les tests ont attrapé.** Une colonne
oubliée : `variation_pct` n'avait pas été renommée en `change_pct` alors que
`db.py` l'attendait. Et la base principale portait encore les anciennes vues,
créées en septembre : il a fallu rejouer les migrations. Les deux ont été
signalés par la suite de tests en moins d'une minute — c'est exactement ce pour
quoi le filet existe.

**Preuve que rien n'a bougé sur le fond** : les chiffres des trois vues du
rapport ont été capturés avant le chantier et comparés après. Identiques au mot
près, pour les sept plats et les huit variations de prix.

**Intégration continue.** Un job `backend` : lint, format, puis les tests avec
un vrai PostgreSQL. Sans la base, 14 tests s'ignorent et la CI validerait un
moteur de calcul jamais exécuté.

**Méthode Git.** Une branche par fonctionnalité depuis `dev`, Pull Request en
français, commits en anglais au format conventionnel, squash and merge. Deux
choses à savoir, apprises à la lecture d'un historique qui avait dérivé : une
règle écrite ne tient que si les réglages du dépôt l'imposent — tant que la
fusion classique reste autorisée, le bouton est là — et mettre une branche à
jour par `git merge dev` laisse un commit de fusion que le squash final
n'efface pas. Le rebase est la seule façon de garder l'historique linéaire.

**Le banc d'essai, étape 2 terminée.** `bench.py` et `scripts/evaluate.py`
complètent le barème. `make eval` sert dès maintenant, sans aucune facture : il
valide les annotations et dit ce qu'il manque pour atteindre les critères de
sortie de COLLECTE.md. Deux décisions dedans : le volume seul ne suffit pas —
dix PDF propres du même jour laissent le corpus « non prêt », faute de série
temporelle et de document difficile — et un document non extrait est compté à
part, car le confondre avec une mauvaise lecture ferait chuter le taux pour une
raison étrangère au modèle. Les extractions sont relues dans
`data/extractions/`, un fichier par document : rejouer le banc après un
changement de barème ne rappelle pas le modèle.

## 11/11/2026 — Facture électronique d'abord, et un établissement inventé

**Le constat.** Deux mois et demi après les premières promesses, `data/raw/`
est vide. Attendre les factures des établissements cobayes bloquait tout le
reste de la chaîne.

**Facture électronique d'abord.** Depuis le 1er septembre 2026, toute
entreprise reçoit ses factures par une plateforme agréée, et les grands
fournisseurs les émettent déjà au format de la norme EN 16931. Un Factur-X est
un PDF qui porte ses propres données en XML : quantités, prix et totaux se
lisent sans modèle, donc sans erreur de lecture. La difficulté se déplace vers
le rapprochement des libellés, qui reste du texte libre. La lecture papier est
reportée, pas abandonnée : les petits fournisseurs ont jusqu'au 1er septembre
2027.

**Un établissement simulé plutôt que des factures tirées au hasard.** Des
factures aléatoires auraient donné un rapport sans histoire. Le jeu part d'un
restaurant-traiteur-pâtissier complet : ses ventes déterminent ses commandes,
qui déterminent ses factures. On sait donc d'avance ce que le rapport doit
montrer, et la chaîne se vérifie de bout en bout. Trois activités, pour
exercer trois formes du modèle : l'assiette, le lot traiteur, la pâtisserie
aux sous-recettes en cascade.

**Des prix relevés, pas inventés.** Les tarifs de Metro sont réservés aux
clients connectés. Les prix viennent donc des cotations publiques du Réseau
des Nouvelles des Marchés (FranceAgriMer) : marchés de gros de Rungis et de
Toulouse, et indicateurs mensuels des prix livrés aux professionnels. 44
produits sur 63 sont sourcés, 19 sont des estimations marquées comme telles.
Le prix du fichier est celui de la dernière semaine ; le chemin qui y mène est
un scénario écrit pour que le rapport ait quelque chose à dire.

**Fictif par construction, pas par convention.** Un SIREN qui échoue au
contrôle de Luhn ne peut désigner aucune entreprise réelle, et le chargeur
refuse un SIREN valide. Une facture factice qui ressemblerait trop à une vraie
serait un faux ; celle-ci ne peut viser personne.

**Ce que la bibliothèque a imposé.** `factur-x` écrit le XML, le contrôle
contre le schéma officiel et sait le relire. Elle n'émet pas le profil BASIC :
tout le jeu est au profil EN 16931. Un avoir s'y écrit en montants positifs,
avec un code de type ; le jeu de vérité le garde en négatif, et le lecteur
devra faire la conversion. L'unité imprimée (« CT ») devient un code (« XCT ») :
la vérité porte le code, puisque c'est lui qui est lu.

**Ce que le jeu ne prouve pas.** Ni que Gramme lit de vraies factures, ni que
quelqu'un paiera pour. Un score parfait sur des documents que nous avons écrits
nous-mêmes ne mesure rien d'autre que la tuyauterie.

**Le tableau de bord entre dans le périmètre**, après la chaîne de lecture et
alimenté par elle.

## 11/11/2026 — Le lecteur de factures électroniques

**Ce qu'il fait.** `facturx.read_invoice` ressort le XML d'un PDF Factur-X et
le rend dans `RawInvoice`, le contrat que le banc note déjà. Aucun modèle de
langage : les champs se lisent par leurs codes de norme.

**Le contrat a dû grandir.** Écrit pour le papier, il ne savait accueillir ni
la référence article, ni les remises et frais de pied de facture. La première
est la clé d'unicité d'un produit fournisseur, et le seul lien entre deux
libellés quand le fournisseur renomme. Les seconds expliquent pourquoi la somme
des lignes n'est pas le total : sans eux, une facture sur huit serait partie en
validation humaine pour un écart que le document justifie lui-même.

**Rien n'est déduit, même ici.** Le total d'une ligne est celui que porte le
XML, pas quantité × prix. Le recoupement arithmétique reste donc un vrai
contrôle : il attraperait un fournisseur dont le logiciel écrit des XML
incohérents.

**Un avoir change de signe à la lecture.** La norme l'écrit en positif avec un
code de type ; un historique de prix l'attend en négatif. La conversion se fait
à un seul endroit, et un prix unitaire ne devient jamais négatif.

**« Pas un Factur-X » n'est pas une panne.** Un PDF sans XML lève `NotFacturX`,
distinct d'un XML refusé par le schéma : le premier est un document d'une autre
nature, à envoyer vers la lecture papier, le second un document cassé.

**Ce que vaut le 100 %.** Les 438 factures du jeu factice se relisent sans un
champ faux. C'est le résultat attendu d'un générateur et d'un lecteur écrits
l'un pour l'autre : il prouve que la chaîne et le barème tiennent, rien sur de
vraies factures. Le banc a été vérifié dans l'autre sens : une extraction
altérée à la main est bien signalée, prix faux et ligne manquante.

## 11/11/2026 — Écrire en base les factures lues

**Le schéma ne savait pas recevoir une facture lue.** Il lui manquait l'unité
de chaque ligne — « 2 CT » et « 12,5 KG » y étaient indiscernables, alors que
tout prix au gramme en dépend —, le taux de TVA par ligne, le total TTC, et
toute place pour les remises et frais de pied de facture. La migration
`003_invoice_detail.sql` les ajoute, et `report_flags` compte désormais le
pied de facture : sans cela, une facture sur huit du jeu factice aurait été
signalée comme incohérente alors que le document s'explique lui-même.

**Un fournisseur se reconnaît à son SIREN, pas à son nom.** La raison sociale
change de présentation d'une facture à l'autre ; deux présentations du même
nom auraient coupé l'historique de prix en deux. Le SIREN est structuré dans
chaque facture électronique. À défaut (papier), on retombe sur le nom exact.

**Un produit sans référence se reconnaît à son libellé.** La contrainte
d'unicité sur (fournisseur, référence) ne protégeait pas les petits
fournisseurs : pour Postgres, deux valeurs vides ne sont jamais égales, et
chaque facture du primeur aurait créé ses produits une nouvelle fois. Un index
unique partiel ferme ce trou. Conséquence assumée : un libellé qui change sans
référence donne un second produit ; c'est à la normalisation de les rattacher
au même ingrédient.

**Tout ou rien, et une issue nommée.** Une facture entre en base entière ou
pas du tout, dans une transaction. Quatre issues : écrite, déjà reçue (même
empreinte), numéro en double (même numéro, autre fichier), à relire (un champ
indispensable non lu). Aucune valeur supposée ne remplace une valeur non lue.

**Aucun prix relevé à cette étape.** Un prix au gramme exige de savoir qu'un
carton de beurre contient 10 kg : c'est le travail de la normalisation. Écrire
un `price_point` maintenant aurait voulu dire en calculer un faux, dans une
table où rien ne se corrige.

**Une troisième base.** Les factures inventées vont dans `gramme_synthetic`,
jamais dans la base principale ; le script refuse d'écrire un jeu factice
ailleurs, ou de vraies factures dans une base de démonstration. Contrôle :
438 factures, 3 161 lignes, 173 774,31 € HT, au centime près du jeu de vérité.

## 12/11/2026 — Le référentiel de l'établissement fictif en base

**Le prérequis de la normalisation.** La base du jeu factice contenait les
factures de Maison Trébuchet, mais pas ce qu'elle cuisine. Sans ingrédients, la
normalisation n'a rien à quoi rattacher un produit fournisseur ; sans recettes,
le rapport n'a rien dont calculer le coût. `referential.py` charge les deux
depuis `restaurant.toml`, sans migration : le schéma d'origine les accueillait
déjà.

**Deux moteurs qui se vérifient l'un l'autre.** Le simulateur calcule en Python
les besoins bruts de chaque produit vendu, rendements et sous-recettes compris ;
le moteur de coût les calcule en SQL, par une requête récursive. Avec un prix
d'un euro par unité de base, le coût d'une portion doit égaler la somme de ses
besoins bruts. Les deux, écrits séparément, s'accordent sur les 22 produits :
c'est la preuve que le référentiel est chargé juste, et que les deux
implémentations disent la même chose.

**Un chargement, pas une mise à jour.** Un second chargement est refusé : il
doublerait le référentiel sans erreur visible. La base factice se recrée de zéro.

**Ce que ce chargement rend visible.** Les recettes sont en base, aucun
ingrédient n'a encore de prix : le moteur de coût les ignore en silence et rend
0 €. C'est le trou connu n° 1, jusqu'ici théorique. Il sera corrigé avant que le
tableau de bord n'affiche le moindre coût.

## 12/11/2026 — Le barème du rapprochement

**Avant le code qui rapproche, ce que « bien rapprocher » veut dire.** Même
discipline que pour la lecture. Pour chaque produit fournisseur, le
normalisateur devra dire à quel ingrédient il correspond — ou qu'il n'en est pas
un, ou qu'il ne sait pas — et ce que contient une unité facturée : un carton de
« BEURRE DOUX 82% 10X1KG » contient 10 000 g. Une erreur sur ce contenu fausse
le prix au gramme d'un facteur entier, sans que rien ne le signale.

**Le chiffre qui fait foi est en euros.** Une erreur sur le beurre, acheté
chaque semaine, ne coûte pas ce que coûte une erreur sur la vanille. Le barème
pèse donc chaque produit par sa dépense nette sur le corpus, avoirs déduits, et
annonce d'abord la part des achats mal valorisée. Le décompte produit par
produit reste affiché dessous, pour savoir lesquels ont cassé.

**Nier un ingrédient est une faute, pas une prudence.** Dire « je ne sais pas »
envoie le produit en validation : c'est une abstention, qui coûte du temps
humain. Dire « ce n'est pas un ingrédient » n'envoie rien à personne : si c'est
faux, le coût du beurre disparaît des recettes en silence. La réponse reprend
les trois états d'un champ lu, et donc la matrice du barème de lecture, sans en
réécrire une.

**Un barème éprouvé avant d'avoir servi.** Trois normalisateurs factices : celui
qui recopie la vérité n'a jamais faux ; celui qui envoie tout en validation n'a
jamais faux mais jamais juste ; celui qui compte un carton de beurre pour un
gramme est faux sur la part exacte des achats de beurre. Un barème qui ne les
distinguerait pas ne mesurerait rien.

**La même clé que la base.** Un produit se reconnaît à son fournisseur (SIREN, à
défaut nom) et à sa référence (à défaut son libellé), exactement comme à
l'écriture : la vérité compte 66 produits, ceux que les factures créent en base.

## 12/11/2026 — Un prix manquant rend le coût inconnu

**Le défaut.** Le moteur de coût rattachait les prix par jointure interne : un
ingrédient sans prix à la date demandée sortait du total, sans un mot. Sur la
base factice, où aucun prix n'existe encore, les 22 plats affichaient un ratio
de 0 %. Avec de vraies fiches techniques, ce serait un ratio simplement trop
bas, que personne ne remarquerait.

**Inconnu plutôt que partiel.** Deux réponses possibles : afficher le coût des
ingrédients connus en le marquant incomplet, ou déclarer le coût inconnu. La
seconde est retenue. Un ratio sous-estimé reste à l'écran et finit lu comme
vrai, alors que la règle du projet est de ne jamais deviner un montant. En SQL,
le coût vaut NULL dès qu'un prix manque, et le NULL se propage seul à la marge
et au ratio. La carte du plat dit « Coût incomplet » et nomme l'ingrédient ;
celui-ci entre une fois dans « À vérifier », quel que soit le nombre de plats
touchés : c'est un prix à obtenir, une seule fois.

**Le même défaut fabriquait de fausses alertes.** Un ingrédient acheté pour la
première fois il y a un mois manquait au coût d'il y a six semaines : le coût
d'avant était trop bas, et le plat partait en alerte. Une dérive dont un bout
est inconnu est maintenant inconnue, et le niveau aussi ; sans cela, le calcul
du niveau aurait rangé le plat en « ok ». La carte dit « Pas de recul » et
montre le ratio du jour.

**Un seul parcours de l'arbre.** La descente récursive des recettes vit
désormais dans une seule fonction, qui rend les ingrédients feuilles avec leur
prix ou sans. Le coût la somme, le rapport y lit les noms manquants : deux
copies de la descente finiraient par ne plus compter la même chose.

**La démo ne bouge pas.** Aucun de ses ingrédients n'est sans prix, ni
aujourd'hui ni il y a six semaines : ses sept plats gardent les mêmes chiffres
au centime, et un test le garantit pour la suite.
