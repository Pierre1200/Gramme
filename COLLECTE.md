# Collecte et annotation — constituer `data/truth/` avant d'écrire l'extraction

*20/08/2026. Phase en cours : collecte de données réelles. Pas de code d'extraction.*

Le `Makefile` prévoit déjà `make eval` (banc d'essai extraction vs `data/truth/`).
Ce document décrit comment remplir ce dossier. **C'est le jeu de vérité qui dira si
l'extraction marche — donc il se constitue avant, pas après.**

## Ce qu'il faut collecter

**La diversité prime sur le volume.** 15 factures bien choisies valent mieux que 100
du même fournisseur au même format.

À couvrir :

- [ ] **Plusieurs fournisseurs** — chacun a sa mise en page, ses libellés, ses unités
- [ ] **Plusieurs qualités de source** — PDF propre, scan, photo au téléphone, bon
      de livraison manuscrit. La plaquette promet de lire les bons manuscrits froissés :
      il en faut dans le jeu de test, sinon la promesse n'est pas testée.
- [ ] **Une série dans le temps chez le même fournisseur** — 4 à 6 factures du même
      fournisseur sur plusieurs mois. **C'est le lot le plus précieux :** le produit
      vend la détection de dérive de prix, donc il faut de l'historique réel, avec de
      vraies variations, pas des factures isolées.
- [ ] **Du salé et du sucré** — les familles de produits et les unités diffèrent
- [ ] **Au moins une facture bien tordue** — remise en pied de page, frais de port,
      consigne, avoir. C'est là que les totaux ne tombent pas juste.

## Confidentialité — à cadrer dès la première demande

Ce sont des données commerciales réelles : prix négociés, volumes, fournisseurs. Un
restaurateur qui te confie ça te confie sa marge.

- Une phrase écrite suffit, mais elle doit exister : *« Vos factures me servent
  uniquement à mettre au point l'outil. Elles ne sont ni partagées, ni montrées à un
  tiers, et supprimées sur simple demande. »* — c'est déjà la phrase de la plaquette,
  garde-la mot pour mot.
- `data/` est intégralement gitignoré. **Vérifier avant chaque commit**, pas après.
- Ne jamais faire figurer un nom d'établissement ou un prix négocié réel dans une
  démo, une capture ou un exemple public.

## Format d'annotation

Un fichier JSON par facture, à côté du document source, calqué sur les tables
`invoice` et `invoice_line`. L'annotation est ce que **toi** tu as lu — la vérité
contre laquelle le modèle sera mesuré.

```
data/
  raw/      2026-08-12_transgourmet_001.pdf
  truth/    2026-08-12_transgourmet_001.json
```

```json
{
  "source_file": "2026-08-12_transgourmet_001.pdf",
  "quality": "photo",
  "regime": "ht",
  "supplier_name": "Transgourmet",
  "invoice_number": "FA2026-88412",
  "invoice_date": "2026-08-12",
  "total_ht": "1043.27",
  "lines": [
    {
      "raw_label": "BEURRE DOUX PLAQUETTE 82% 10X500G",
      "supplier_ref": "40218",
      "qty": "2",
      "raw_unit": "CT",
      "unit_price_ht": "43.80",
      "line_total_ht": "87.60",
      "pack_qty": "5",
      "base_unit": "kg",
      "unreadable": [],
      "notes": "colisage 10x500g = 5 kg par colis, non explicite sur la ligne"
    }
  ],
  "pieges": [
    "remise de pied de page -2% appliquée après le total lignes",
    "frais de port 12,50 € sur une ligne sans quantité"
  ]
}
```

### Absent ou illisible : ne pas confondre

`null` tout seul ne suffit pas — il ne dit pas si le champ manque au document ou si
tu n'as pas réussi à le lire. Or ces deux cas se notent différemment au banc : un
modèle qui répond ABSENT sur un ticket sans numéro de facture a **raison**, et le
pénaliser reviendrait à lui apprendre à deviner.

- **Absent du document** → `null`, rien de plus. Un ticket de caisse n'a pas de
  numéro de facture, point.
- **Présent mais illisible** → `null` **et** le nom du champ dans `unreadable` :

```json
{
  "raw_label": "BEUR?E D??X 25KG",
  "qty": "2",
  "unit_price_ht": null,
  "unreadable": ["unit_price_ht"],
  "notes": "chiffre mangé par un pli"
}
```

Ces cas-là valent de l'or : ce sont eux qui doivent partir en file de validation
humaine plutôt que d'être devinés (`FieldStatus.UNREADABLE` dans `models.py`).

### `regime` : HT ou TTC

Une facture fournisseur est en HT, un ticket de caisse en TTC. Et un
auto-entrepreneur en franchise en base **ne récupère pas la TVA** — son coût réel
est le TTC. Annote ce qui est imprimé, sans jamais convertir, et dis dans `regime`
de quoi il s'agit. Se tromper ici fausse tous les coûts de 5,5 à 20 %, en silence.

### Vérifier une annotation

Le format est validé par `src/gramme/truth.py`. Dès qu'un fichier est écrit :

```bash
uv run python -c "from gramme.truth import load_truth; print(len(load_truth()), 'annotations valides')"
```

Il échoue en nommant le fichier fautif : champ inconnu, champ déclaré illisible qui
porte quand même une valeur, ou document source introuvable dans `data/raw/`.

## Ce qu'il faut observer en annotant — plus important que les données elles-mêmes

L'OCR n'est pas le vrai problème. Les vrais problèmes apparaissent en annotant à la
main, et c'est pour ça que cette phase ne se saute pas :

1. **Le colisage.** « 10X500G » à 43,80 € — c'est 43,80 le colis ou le kilo ? Sans
   réponse, le prix au kilo est faux, donc tout le coût matière est faux.
2. **La normalisation des unités.** Pièce, kg, L, colis, barquette. Le `price_point`
   est en `price_per_base_unit` : il faut savoir comment y arriver depuis chaque
   libellé.
3. **Le rapprochement libellé → ingrédient.** « BEURRE DOUX PLAQUETTE 82% », « BEURRE
   DX 82% 500G », « B.DOUX PLAQ » chez trois fournisseurs = le même beurre. C'est ça,
   `supplier_product` → `ingredient`, et c'est probablement **la partie la plus dure de
   tout le produit** — plus que la lecture du document.
4. **Les totaux qui ne tombent pas.** Remises, port, consignes, avoirs. Ton
   `INVOICE_TOTAL_TOLERANCE` et son commentaire anticipent déjà ça : un MISMATCH au
   niveau facture signale « regarde », pas « faux ». Les données réelles diront si la
   tolérance est bien calibrée.
5. **Les tolérances d'arrondi.** Le raisonnement dans `models.py` (absolu plutôt que
   relatif, à cause de l'arrondi au centime par ligne) est juste — mais il porte sa
   propre limite écrite : si le fournisseur imprime un prix unitaire déjà arrondi,
   l'erreur croît avec la quantité. **Les factures réelles vont te dire si le plancher
   à 0,02 € tient.** C'est exactement ce que `scripts/evaluate.py` doit mesurer.

Note tout ça au fil de l'eau dans un carnet. Ces observations valent plus que le
volume de données.

## Ne pas oublier la fiche technique

L'offre de la plaquette n'est pas « donnez-moi vos factures ». C'est **« une facture,
une fiche technique, je calcule le coût matière exact du plat de votre choix, parage
compris, et je repasse vous montrer »**.

Repartir avec les factures seules te donne de la donnée mais pas de conversation.
Repartir avec **facture + fiche technique** t'engage à revenir — et c'est ce
deuxième rendez-vous qui transforme un contact en client. Le calcul se fait au
tableur, à la main, avec tes propres connaissances de cuisinier. Aucun code requis.

## Contacts chauds à activer

| Piste | Via | Note |
|---|---|---|
| Le Bowling du Rouergue | ton père (ancien assureur, ami du dirigeant) | Gros établissement local, volumes réels, plusieurs fournisseurs. Excellent premier cas. |
| Anciens employeurs | La Maison des Thés, Les Jardins de l'Acropolis, Le Sabot de Noël | Ils te connaissent. Salé **et** sucré — exactement la diversité recherchée. |
| Anciens collègues, fournisseurs, commerciaux | réseau direct | Les commerciaux fournisseurs voient toutes les factures de la place. |
| Voisins de la pépinière | open space Rodez Agglo | Pas des restaurateurs, mais des gens qui connaissent des restaurateurs. |

Une intro chaude vaut vingt portes froides. Commence par là — et n'oublie pas de
demander à chaque fois : *« vous connaissez quelqu'un d'autre à qui ça pourrait
servir ? »*

## Le critère de sortie

On arrête la collecte et on commence à coder quand :

- [ ] `data/truth/` contient **10 à 15 factures annotées**, dont au moins une série
      temporelle chez un même fournisseur et au moins un document manuscrit ou photo
- [ ] Les 5 problèmes ci-dessus sont documentés avec des exemples réels
- [ ] **Au moins une personne a demandé « quand est-ce que je peux l'avoir, et combien
      ça coûte ? »**

Le troisième point est le vrai. Les deux premiers disent que l'outil est faisable ;
seul le troisième dit qu'il est utile.
