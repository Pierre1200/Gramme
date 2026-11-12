-- =====================================================================
--  004 — Un ingrédient sans prix rend le coût INCONNU, pas plus bas.
--
--  Jusqu'ici, recipe_cost_at rattachait les prix par jointure interne : un
--  ingrédient sans prix à la date demandée disparaissait du total, et le
--  coût était sous-estimé sans avertissement. Décision 5 appliquée au
--  calcul : on ne devine pas un montant. Un coût incomplet vaut NULL, le
--  rapport nomme l'ingrédient manquant et le met dans « À vérifier ».
--
--  Même défaut, effet plus discret : un ingrédient acheté pour la première
--  fois il y a un mois manquait au coût d'il y a six semaines, ce qui
--  fabriquait une dérive, donc une fausse alerte. Une dérive dont l'un des
--  deux bouts est inconnu est désormais inconnue.
-- =====================================================================


-- ---------------------------------------------------------------------
--  1. LES FEUILLES DE L'ARBRE, AVEC LEUR PRIX OU SANS
-- ---------------------------------------------------------------------
-- L'unique parcours récursif des recettes : le coût le somme, le rapport y
-- lit les ingrédients sans prix. Deux copies de la descente finiraient par
-- ne plus compter la même chose.
--
-- Une ligne par composant ingrédient rencontré : un ingrédient présent dans
-- deux sous-recettes donne deux lignes, chacune avec sa quantité.

CREATE OR REPLACE FUNCTION recipe_cost_lines_at(p_recipe_id BIGINT, p_as_of DATE)
RETURNS TABLE (
    ingredient_id       BIGINT,
    raw_qty             NUMERIC,   -- quantité BRUTE à acheter, en unité de base
    price_per_base_unit NUMERIC    -- NULL : aucun prix connu à cette date
) AS $$
WITH RECURSIVE cost_tree AS (
    -- Ancre : la recette racine, consommée à 100 %
    SELECT  r.id            AS recipe_id,
            1.0::NUMERIC    AS multiplier,
            0               AS depth,
            ARRAY[r.id]     AS path
    FROM    recipe r
    WHERE   r.id = p_recipe_id

    UNION ALL

    -- Récursion : la fraction d'un batch de sous-recette réellement consommée
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
price_at_date AS (
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
SELECT  rc.ingredient_id,
        ct.multiplier * (rc.qty / rc.yield_ratio),
        px.price_per_base_unit
FROM    cost_tree ct
JOIN    recipe_component rc ON rc.parent_recipe_id = ct.recipe_id
                          AND rc.ingredient_id IS NOT NULL
-- Jointure EXTERNE : l'ingrédient sans prix reste, avec un prix NULL.
LEFT JOIN price_at_date px ON px.ingredient_id = rc.ingredient_id;
$$ LANGUAGE SQL STABLE;


-- ---------------------------------------------------------------------
--  2. LE COÛT : NULL DÈS QU'UN PRIX MANQUE
-- ---------------------------------------------------------------------
-- recipe_cost (« aujourd'hui ») et recipe_margin s'appuient dessus : le NULL
-- s'y propage sans qu'il faille les réécrire. Une recette sans aucun
-- composant coûte toujours 0 : rien ne manque.

CREATE OR REPLACE FUNCTION recipe_cost_at(p_recipe_id BIGINT, p_as_of DATE)
RETURNS NUMERIC AS $$
    SELECT CASE
               WHEN bool_or(l.price_per_base_unit IS NULL) THEN NULL
               ELSE COALESCE(SUM(l.raw_qty * l.price_per_base_unit), 0)
           END
    FROM   recipe_cost_lines_at(p_recipe_id, p_as_of) l;
$$ LANGUAGE SQL STABLE;


-- ---------------------------------------------------------------------
--  3. LES MARGES DU RAPPORT
-- ---------------------------------------------------------------------
-- Colonnes de 002 reprises à l'identique et dans le même ordre (contrainte de
-- CREATE OR REPLACE VIEW), plus missing_prices en fin de liste.
--
-- Le niveau est NULL quand la dérive est inconnue : sans cela, le CASE
-- tomberait dans « ok » et un plat dont on ignore le coût serait déclaré
-- stable.

CREATE OR REPLACE VIEW report_margins AS
WITH base AS (
    SELECT  r.id,
            r.name,
            r.portions,
            r.selling_price_ttc,
            r.selling_price_ttc / (1 + r.vat_rate)                           AS price_excl_vat,
            recipe_cost(r.id) / NULLIF(r.portions, 0)                        AS cost,
            recipe_cost_at(r.id, CURRENT_DATE - 42) / NULLIF(r.portions, 0)  AS cost_before,
            m.missing_prices
    FROM    recipe r
    -- Les ingrédients qui rendent le coût d'aujourd'hui inconnu, par leur nom.
    CROSS JOIN LATERAL (
        SELECT  COALESCE(array_agg(DISTINCT i.name ORDER BY i.name), '{}') AS missing_prices
        FROM    recipe_cost_lines_at(r.id, CURRENT_DATE) l
        JOIN    ingredient i ON i.id = l.ingredient_id
        WHERE   l.price_per_base_unit IS NULL
    ) m
    WHERE   r.is_sellable
),
ratios AS (
    SELECT  base.*,
            100 * cost        / NULLIF(price_excl_vat, 0) AS ratio,
            100 * cost_before / NULLIF(price_excl_vat, 0) AS ratio_before
    FROM    base
)
SELECT  id                                  AS recipe_id,
        name                                AS recipe,
        portions,
        selling_price_ttc                   AS selling_price_incl_vat,
        ROUND(price_excl_vat, 2)            AS selling_price,
        ROUND(cost, 2)                      AS cost_per_portion,
        ROUND(price_excl_vat - cost, 2)     AS margin_per_portion,
        ROUND(price_excl_vat - cost_before, 2) AS margin_per_portion_before,
        ROUND(ratio, 1)                     AS ratio,
        ROUND(ratio_before, 1)              AS ratio_before,
        ROUND(ratio - ratio_before, 1)      AS drift_points,
        -- Seuils de présentation, en points de ratio gagnés sur 6 semaines.
        CASE
            WHEN ratio - ratio_before IS NULL THEN NULL
            WHEN ratio - ratio_before >= 2.0  THEN 'alerte'
            WHEN ratio - ratio_before >= 0.5  THEN 'vigilance'
            ELSE 'ok'
        END                                 AS level,
        missing_prices
FROM    ratios
ORDER BY ratio - ratio_before DESC NULLS LAST, name;


-- ---------------------------------------------------------------------
--  4. CE QUI DEMANDE UN REGARD HUMAIN : UN TROISIÈME MOTIF
-- ---------------------------------------------------------------------
-- Les deux motifs de 003 sont repris à l'identique. S'ajoute missing_price :
-- un ingrédient sans prix aujourd'hui, utilisé par au moins un plat vendu,
-- même au travers d'une sous-recette. Une seule ligne par ingrédient, quel que
-- soit le nombre de plats touchés : c'est un prix à obtenir, une seule fois.

CREATE OR REPLACE VIEW report_flags AS
SELECT  'label_to_confirm'::TEXT  AS reason,
        sp.raw_label                 AS label,
        s.name                       AS supplier,
        NULL::DATE                   AS invoice_date,
        NULL::NUMERIC                AS gap
FROM    supplier_product sp
JOIN    supplier s ON s.id = sp.supplier_id
WHERE   sp.status = 'pending'

UNION ALL

SELECT  'total_mismatch',
        COALESCE(i.invoice_number, '(sans numéro)'),
        s.name,
        i.invoice_date,
        ROUND(ABS(t.counted - i.total_ht), 2)
FROM    invoice i
JOIN    supplier s ON s.id = i.supplier_id
CROSS JOIN LATERAL (
    SELECT COALESCE((SELECT SUM(il.line_total_ht) FROM invoice_line il
                     WHERE il.invoice_id = i.id), 0)
         + COALESCE((SELECT SUM(ia.amount_ht) FROM invoice_adjustment ia
                     WHERE ia.invoice_id = i.id), 0) AS counted
) t
WHERE   i.total_ht IS NOT NULL
  AND   ABS(t.counted - i.total_ht) > 0.05

UNION ALL

SELECT  'missing_price',
        i.name,
        NULL,
        NULL,
        NULL
FROM    ingredient i
WHERE   EXISTS (
            SELECT 1
            FROM   recipe r
            CROSS JOIN LATERAL recipe_cost_lines_at(r.id, CURRENT_DATE) l
            WHERE  r.is_sellable
              AND  l.ingredient_id = i.id
              AND  l.price_per_base_unit IS NULL
        );
