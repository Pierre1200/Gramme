-- =====================================================================
--  002 — Ce que le rapport du lundi affiche, calculé en base.
--
--  Décision 2 : aucun montant, ratio ou écart n'est calculé ailleurs
--  qu'ici. Les vues rapport_* sont prêtes à afficher : la couche web ne
--  fait que mettre en forme (virgule décimale), jamais d'arithmétique.
-- =====================================================================


COMMENT ON COLUMN recipe.selling_price_ttc IS
    'Prix de vente TTC d''UNE portion, pas du batch. recipe_margin divise le '
    'coût par portions mais pas ce prix : saisir 600 € pour une paella de 50 '
    'au lieu de 12 € donne un ratio de 0,4 %.';


-- ---------------------------------------------------------------------
--  1. COÛT D'UNE RECETTE À UNE DATE DONNÉE
-- ---------------------------------------------------------------------
-- Même descente récursive que recipe_cost (001), mais chaque ingrédient est
-- valorisé au dernier prix connu À CETTE DATE. C'est ce qui permet au
-- rapport de dire « il y a 6 semaines, ce plat vous coûtait… ».

CREATE OR REPLACE FUNCTION recipe_cost_at(p_recipe_id BIGINT, p_as_of DATE)
RETURNS NUMERIC AS $$
WITH RECURSIVE cost_tree AS (
    SELECT  r.id            AS recipe_id,
            1.0::NUMERIC    AS multiplier,
            0               AS depth,
            ARRAY[r.id]     AS path
    FROM    recipe r
    WHERE   r.id = p_recipe_id

    UNION ALL

    SELECT  rc.child_recipe_id,
            ct.multiplier * (rc.qty / rc.yield_ratio) / child.yield_qty,
            ct.depth + 1,
            ct.path || rc.child_recipe_id
    FROM    cost_tree ct
    JOIN    recipe_component rc ON rc.parent_recipe_id = ct.recipe_id
    JOIN    recipe child        ON child.id = rc.child_recipe_id
    WHERE   rc.child_recipe_id IS NOT NULL
      AND   NOT (rc.child_recipe_id = ANY(ct.path))   -- anti-cycle
      AND   ct.depth < 10                             -- garde-fou
),
prix_a_date AS (
    SELECT DISTINCT ON (sp.ingredient_id)
           sp.ingredient_id,
           pp.price_per_base_unit
    FROM   price_point pp
    JOIN   supplier_product sp ON sp.id = pp.supplier_product_id
    WHERE  sp.ingredient_id IS NOT NULL
      AND  sp.status = 'confirmed'
      AND  pp.observed_on <= p_as_of
    ORDER BY sp.ingredient_id, pp.observed_on DESC, pp.id DESC
)
SELECT COALESCE(SUM(
           ct.multiplier
         * (rc.qty / rc.yield_ratio)       -- quantité BRUTE à acheter
         * px.price_per_base_unit
       ), 0)
FROM   cost_tree ct
JOIN   recipe_component rc ON rc.parent_recipe_id = ct.recipe_id
                          AND rc.ingredient_id IS NOT NULL
-- LIMITE CONNUE : jointure interne. Un ingrédient sans prix à cette date
-- DISPARAÎT du total, sans avertissement : le coût est sous-estimé.
-- Documenté dans CLAUDE.md, verrouillé par tests/test_cost.py, pas corrigé.
JOIN   prix_a_date px ON px.ingredient_id = rc.ingredient_id;
$$ LANGUAGE SQL STABLE;


-- recipe_cost devient le cas particulier « aujourd'hui ». Une seule
-- implémentation du parcours récursif : deux copies finiraient par diverger.
CREATE OR REPLACE FUNCTION recipe_cost(p_recipe_id BIGINT)
RETURNS NUMERIC AS $$
    SELECT recipe_cost_at(p_recipe_id, CURRENT_DATE);
$$ LANGUAGE SQL STABLE;


-- ---------------------------------------------------------------------
--  2. MARGE BRUTE EN EUROS DANS recipe_margin
-- ---------------------------------------------------------------------
-- CREATE OR REPLACE VIEW n'autorise l'ajout de colonnes qu'en FIN de liste :
-- les colonnes de 001 sont reprises à l'identique, dans le même ordre.

CREATE OR REPLACE VIEW recipe_margin AS
SELECT  r.id,
        r.name,
        r.portions,
        r.selling_price_ttc,
        ROUND(r.selling_price_ttc / (1 + r.vat_rate), 2)      AS price_ht,
        ROUND(recipe_cost(r.id), 4)                           AS batch_cost_ht,
        ROUND(recipe_cost(r.id) / NULLIF(r.portions, 0), 4)   AS unit_cost_ht,
        ROUND(
            100 * recipe_cost(r.id) / NULLIF(r.portions, 0)
            / NULLIF(r.selling_price_ttc / (1 + r.vat_rate), 0)
        , 1)                                                  AS food_cost_pct,
        ROUND(
            r.selling_price_ttc / (1 + r.vat_rate)
            - recipe_cost(r.id) / NULLIF(r.portions, 0)
        , 2)                                                  AS marge_brute_ht
FROM    recipe r
WHERE   r.is_sellable;


-- ---------------------------------------------------------------------
--  3. VUES DU RAPPORT
-- ---------------------------------------------------------------------

-- Une ligne par plat vendu : où en est sa marge, et comment elle a bougé
-- depuis 6 semaines. Le niveau d'alerte porte sur la DÉRIVE du ratio, pas sur
-- un seuil absolu : une pâtisserie saine est à 22 %, une viande saine à 35 %,
-- mais un plat qui prend 3 points en six semaines mérite qu'on regarde, quel
-- que soit le métier.
--
-- Le prix de vente d'il y a 6 semaines n'est pas historisé : les deux ratios
-- sont calculés sur le prix de vente actuel. L'écart mesure donc bien la
-- dérive des coûts, pas un changement de carte.
CREATE OR REPLACE VIEW rapport_marges AS
WITH base AS (
    SELECT  r.id,
            r.name,
            r.portions,
            r.selling_price_ttc,
            r.selling_price_ttc / (1 + r.vat_rate)                           AS prix_ht,
            recipe_cost(r.id) / NULLIF(r.portions, 0)                        AS cout,
            recipe_cost_at(r.id, CURRENT_DATE - 42) / NULLIF(r.portions, 0)  AS cout_avant
    FROM    recipe r
    WHERE   r.is_sellable
),
ratios AS (
    SELECT  base.*,
            100 * cout       / NULLIF(prix_ht, 0) AS ratio,
            100 * cout_avant / NULLIF(prix_ht, 0) AS ratio_avant
    FROM    base
)
SELECT  id                                  AS recette_id,
        name                                AS recette,
        portions,
        selling_price_ttc                   AS prix_vente_ttc,
        ROUND(prix_ht, 2)                   AS prix_vente_ht,
        ROUND(cout, 2)                      AS cout_portion,
        ROUND(prix_ht - cout, 2)            AS marge_portion,
        ROUND(prix_ht - cout_avant, 2)      AS marge_portion_avant,
        ROUND(ratio, 1)                     AS ratio,
        ROUND(ratio_avant, 1)               AS ratio_avant,
        ROUND(ratio - ratio_avant, 1)       AS ecart_points,
        -- Seuils de présentation, en points de ratio gagnés sur 6 semaines.
        CASE
            WHEN ratio - ratio_avant >= 2.0 THEN 'alerte'
            WHEN ratio - ratio_avant >= 0.5 THEN 'vigilance'
            ELSE 'ok'
        END                                 AS niveau
FROM    ratios
ORDER BY ratio - ratio_avant DESC, name;


-- Les prix qui ont bougé de plus de 3 % sur 6 semaines (seuil de price_drift),
-- ramenés de l'unité de base à l'unité qu'on lit sur une mercuriale :
-- €/g -> €/kg, €/ml -> €/L. La conversion est un calcul, donc elle est ici.
CREATE OR REPLACE VIEW rapport_variations AS
SELECT  d.ingredient_id,
        d.ingredient_name                    AS ingredient,
        ROUND(d.old_price * u.facteur, 2)    AS prix_avant,
        ROUND(d.new_price * u.facteur, 2)    AS prix_apres,
        u.libelle                            AS unite,
        d.variation_pct
FROM    price_drift(42) d
JOIN    ingredient i ON i.id = d.ingredient_id
JOIN    (VALUES ('g'::base_unit,    1000, 'kg'),
                ('ml'::base_unit,   1000, 'L'),
                ('unit'::base_unit,    1, 'pièce')
        ) AS u(base_unit, facteur, libelle) ON u.base_unit = i.base_unit
ORDER BY d.variation_pct DESC;


-- Ce qui demande un regard humain. Deux motifs pour l'instant :
--  - un libellé fournisseur que le système n'a pas su rattacher à un
--    ingrédient (file de validation, décision 5) ;
--  - une facture dont la somme des lignes ne retombe pas sur le total HT.
--    Tolérance 0,05 € : la même que INVOICE_TOTAL_TOLERANCE dans models.py.
--    Si l'une bouge, l'autre doit suivre.
CREATE OR REPLACE VIEW rapport_attention AS
SELECT  'libelle_a_confirmer'::TEXT  AS motif,
        sp.raw_label                 AS libelle,
        s.name                       AS fournisseur,
        NULL::DATE                   AS date_facture,
        NULL::NUMERIC                AS ecart
FROM    supplier_product sp
JOIN    supplier s ON s.id = sp.supplier_id
WHERE   sp.status = 'pending'

UNION ALL

SELECT  'total_incoherent',
        COALESCE(i.invoice_number, '(sans numéro)'),
        s.name,
        i.invoice_date,
        ROUND(ABS(COALESCE(SUM(il.line_total_ht), 0) - i.total_ht), 2)
FROM    invoice i
JOIN    supplier s ON s.id = i.supplier_id
LEFT JOIN invoice_line il ON il.invoice_id = i.id
WHERE   i.total_ht IS NOT NULL
GROUP BY i.id, s.name
HAVING  ABS(COALESCE(SUM(il.line_total_ht), 0) - i.total_ht) > 0.05;


-- Les trois compteurs affichés en tête de rapport. Seules les ALERTES comptent
-- comme plats à surveiller : la vigilance reste visible plat par plat, mais
-- l'additionner gonflerait la tuile (« 5 plats sur 7 ») et banaliserait l'alerte.
CREATE OR REPLACE VIEW rapport_resume AS
SELECT  (SELECT count(*) FROM rapport_marges     WHERE niveau = 'alerte')  AS plats_en_alerte,
        (SELECT count(*) FROM rapport_variations WHERE variation_pct > 0)  AS prix_en_hausse,
        (SELECT count(*) FROM rapport_attention)                           AS points_a_verifier;
