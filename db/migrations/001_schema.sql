-- =====================================================================
--  Prix de revient pâtisserie / restauration
--  Schéma PostgreSQL — sous-recettes récursives, rendements, historique
--  de prix immuable.
--
--  Principes structurants :
--    1. Une seule table `recipe` : une sous-recette EST une recette.
--    2. `recipe_component` = arête du graphe (ingrédient XOR sous-recette).
--    3. Le rendement (yield_ratio) est porté par le COMPOSANT, pas par
--       l'ingrédient : un citron rend 0.35 en jus et 0.03 en zeste.
--    4. Les prix ne sont JAMAIS mis à jour, seulement insérés.
--    5. Tout est stocké en unité de base (g / ml / pièce).
-- =====================================================================

CREATE TYPE base_unit AS ENUM ('g', 'ml', 'unit');
CREATE TYPE mapping_status AS ENUM ('pending', 'confirmed', 'rejected');


-- ---------------------------------------------------------------------
--  1. FOURNISSEURS ET RÉFÉRENTIEL PRODUIT
-- ---------------------------------------------------------------------

CREATE TABLE supplier (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- L'intrant canonique, indépendant du fournisseur.
-- Ex : « Beurre doux 82% MG ». C'est TON référentiel, ton actif métier.
CREATE TABLE ingredient (
    id                BIGSERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    base_unit         base_unit NOT NULL,
    -- Densité : indispensable dès qu'une recette exprime en volume un
    -- produit acheté au poids (crème, miel, glucose, huile...).
    density_g_per_ml  NUMERIC(10,4),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Le SKU tel qu'il apparaît chez UN fournisseur.
-- « BEURRE DOUX PLAQUE 25KG ELLE&VIRE » -> ingredient « Beurre doux 82% MG »
-- C'est ici que le LLM travaille : il propose le mapping, l'humain valide.
CREATE TABLE supplier_product (
    id            BIGSERIAL PRIMARY KEY,
    supplier_id   BIGINT NOT NULL REFERENCES supplier(id),
    supplier_ref  TEXT,                       -- code article fournisseur
    raw_label     TEXT NOT NULL,              -- libellé brut de la facture
    ingredient_id BIGINT REFERENCES ingredient(id),   -- NULL = non normalisé

    -- Contenu d'un colis, converti en unité de base.
    -- Ex : carton de 6 x 1 L de crème -> 6000, 'ml'
    pack_qty      NUMERIC(12,4),
    pack_unit     base_unit,

    -- Traçabilité de la décision automatique
    confidence    NUMERIC(3,2),               -- 0.00 à 1.00, sortie du modèle
    status        mapping_status NOT NULL DEFAULT 'pending',
    mapped_by     TEXT,                       -- nom du modèle ou 'human'
    mapped_at     TIMESTAMPTZ,

    UNIQUE (supplier_id, supplier_ref)
);

CREATE INDEX ON supplier_product (ingredient_id);
-- File d'attente de validation : la requête que ton interface appelle.
CREATE INDEX ON supplier_product (status) WHERE status = 'pending';


-- ---------------------------------------------------------------------
--  2. FACTURES (documents sources)
-- ---------------------------------------------------------------------

CREATE TABLE invoice (
    id              BIGSERIAL PRIMARY KEY,
    supplier_id     BIGINT NOT NULL REFERENCES supplier(id),
    invoice_number  TEXT,
    invoice_date    DATE NOT NULL,
    total_ht        NUMERIC(12,2),

    -- Traçabilité : on doit pouvoir remonter à l'image d'origine.
    source_uri      TEXT NOT NULL,
    source_hash     TEXT NOT NULL UNIQUE,     -- anti-doublon (même PDF renvoyé)
    extracted_at    TIMESTAMPTZ,
    extraction_model TEXT,                    -- ex : 'MiniMax-M3'

    UNIQUE (supplier_id, invoice_number)
);

CREATE TABLE invoice_line (
    id                  BIGSERIAL PRIMARY KEY,
    invoice_id          BIGINT NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    line_no             INT,
    raw_label           TEXT NOT NULL,
    supplier_product_id BIGINT REFERENCES supplier_product(id),

    qty                 NUMERIC(12,4) NOT NULL,   -- quantité facturée
    unit_price_ht       NUMERIC(12,4) NOT NULL,
    line_total_ht       NUMERIC(12,2),
    confidence          NUMERIC(3,2)
);

CREATE INDEX ON invoice_line (invoice_id);
CREATE INDEX ON invoice_line (supplier_product_id);


-- ---------------------------------------------------------------------
--  3. HISTORIQUE DE PRIX  (append-only : jamais d'UPDATE)
-- ---------------------------------------------------------------------

CREATE TABLE price_point (
    id                  BIGSERIAL PRIMARY KEY,
    supplier_product_id BIGINT NOT NULL REFERENCES supplier_product(id),
    observed_on         DATE NOT NULL,

    -- Prix RAMENÉ À L'UNITÉ DE BASE. C'est la seule façon de comparer
    -- un colis de 25 kg et une plaque au kg.
    price_per_base_unit NUMERIC(14,6) NOT NULL,

    invoice_line_id     BIGINT REFERENCES invoice_line(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON price_point (supplier_product_id, observed_on DESC);

-- Empêche physiquement l'écrasement d'un prix.
CREATE RULE price_point_no_update AS ON UPDATE TO price_point DO INSTEAD NOTHING;


-- ---------------------------------------------------------------------
--  4. RECETTES ET SOUS-RECETTES
-- ---------------------------------------------------------------------

CREATE TABLE recipe (
    id                BIGSERIAL PRIMARY KEY,
    name              TEXT NOT NULL,

    -- Ce que produit UN batch de cette recette.
    -- Ex : crème pâtissière -> 1000 g. Pâte feuilletée -> 2400 g.
    -- Intègre la perte de cuisson : si 1000 g de pâte crue donnent 920 g
    -- cuits, yield_qty = 920.
    yield_qty         NUMERIC(12,4) NOT NULL CHECK (yield_qty > 0),
    yield_unit        base_unit NOT NULL,

    -- false = sous-recette (crème pât', praliné, insert)
    -- true  = produit fini vendu en vitrine
    is_sellable       BOOLEAN NOT NULL DEFAULT false,
    portions          INT CHECK (portions IS NULL OR portions > 0),
    selling_price_ttc NUMERIC(10,2),
    vat_rate          NUMERIC(4,3) DEFAULT 0.055,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (NOT is_sellable OR selling_price_ttc IS NOT NULL)
);


-- L'arête du graphe. Une ligne = un composant d'une recette.
CREATE TABLE recipe_component (
    id               BIGSERIAL PRIMARY KEY,
    parent_recipe_id BIGINT NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,

    -- XOR : soit un ingrédient, soit une sous-recette.
    ingredient_id    BIGINT REFERENCES ingredient(id),
    child_recipe_id  BIGINT REFERENCES recipe(id),

    -- Quantité NETTE utilisée dans la recette (ce que tu pèses).
    qty              NUMERIC(12,4) NOT NULL CHECK (qty > 0),
    unit             base_unit NOT NULL,

    -- Rendement net/brut. 1.0 par défaut.
    -- Jus de citron : 0.35  |  zeste : 0.03  |  fraises équeutées : 0.90
    -- Le coût se calcule sur le BRUT : qty / yield_ratio.
    yield_ratio      NUMERIC(6,4) NOT NULL DEFAULT 1.0
                     CHECK (yield_ratio > 0 AND yield_ratio <= 1),

    note             TEXT,

    CONSTRAINT component_is_xor CHECK (
        (ingredient_id IS NOT NULL AND child_recipe_id IS NULL)
     OR (ingredient_id IS NULL     AND child_recipe_id IS NOT NULL)
    ),
    CONSTRAINT no_self_reference CHECK (parent_recipe_id IS DISTINCT FROM child_recipe_id)
);

CREATE INDEX ON recipe_component (parent_recipe_id);
CREATE INDEX ON recipe_component (child_recipe_id);


-- ---------------------------------------------------------------------
--  5. PRIX COURANT PAR INGRÉDIENT
-- ---------------------------------------------------------------------
-- V1 : dernier prix confirmé, tous fournisseurs confondus.
-- Limite connue : si le client achète son beurre chez deux fournisseurs,
-- le dernier prix observé n'est pas forcément le prix pertinent.
-- V2 : moyenne pondérée par les volumes des 90 derniers jours.

CREATE VIEW ingredient_current_cost AS
SELECT DISTINCT ON (sp.ingredient_id)
       sp.ingredient_id,
       pp.price_per_base_unit,
       pp.observed_on,
       sp.supplier_id
FROM   price_point pp
JOIN   supplier_product sp ON sp.id = pp.supplier_product_id
WHERE  sp.ingredient_id IS NOT NULL
  AND  sp.status = 'confirmed'
ORDER BY sp.ingredient_id, pp.observed_on DESC, pp.id DESC;


-- ---------------------------------------------------------------------
--  6. LE CŒUR : CALCUL RÉCURSIF DU COÛT MATIÈRE
-- ---------------------------------------------------------------------
-- Descend l'arbre des sous-recettes en propageant un « multiplicateur » :
-- la fraction d'un batch de la sous-recette réellement consommée.
--
--   Crème pâtissière : yield_qty = 1000 g
--   La tarte en utilise 400 g  ->  multiplier = 0.4
--   Donc 40 % du coût du batch de crème est imputé à la tarte.
--
-- Protection anti-cycle par tableau de chemin + garde-fou de profondeur.

CREATE OR REPLACE FUNCTION recipe_cost(p_recipe_id BIGINT)
RETURNS NUMERIC AS $$
WITH RECURSIVE cost_tree AS (
    -- Ancre : la recette racine, consommée à 100 %
    SELECT  r.id                  AS recipe_id,
            1.0::NUMERIC          AS multiplier,
            0                     AS depth,
            ARRAY[r.id]           AS path
    FROM    recipe r
    WHERE   r.id = p_recipe_id

    UNION ALL

    -- Récursion : pour chaque sous-recette rencontrée
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
)
-- On ne somme que les FEUILLES (les ingrédients réels).
SELECT COALESCE(SUM(
           ct.multiplier
         * (rc.qty / rc.yield_ratio)       -- quantité BRUTE à acheter
         * icc.price_per_base_unit
       ), 0)
FROM   cost_tree ct
JOIN   recipe_component rc  ON rc.parent_recipe_id = ct.recipe_id
                           AND rc.ingredient_id IS NOT NULL
JOIN   ingredient_current_cost icc ON icc.ingredient_id = rc.ingredient_id;
$$ LANGUAGE SQL STABLE;


-- Tableau de bord des marges : la requête qui alimente le rapport hebdo.
CREATE VIEW recipe_margin AS
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
        , 1)                                                  AS food_cost_pct
FROM    recipe r
WHERE   r.is_sellable;


-- ---------------------------------------------------------------------
--  7. DÉTECTION DE DÉRIVE  (le produit, en une requête)
-- ---------------------------------------------------------------------
-- Variation de prix par ingrédient entre deux fenêtres temporelles.

CREATE OR REPLACE FUNCTION price_drift(p_days INT DEFAULT 42)
RETURNS TABLE (
    ingredient_id BIGINT,
    ingredient_name TEXT,
    old_price NUMERIC,
    new_price NUMERIC,
    variation_pct NUMERIC
) AS $$
    WITH ranked AS (
        SELECT sp.ingredient_id,
               pp.price_per_base_unit,
               pp.observed_on,
               FIRST_VALUE(pp.price_per_base_unit) OVER w AS latest,
               LAST_VALUE(pp.price_per_base_unit)  OVER w AS earliest
        FROM   price_point pp
        JOIN   supplier_product sp ON sp.id = pp.supplier_product_id
        WHERE  sp.status = 'confirmed'
          AND  pp.observed_on >= CURRENT_DATE - p_days
        WINDOW w AS (
            PARTITION BY sp.ingredient_id
            ORDER BY pp.observed_on DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )
    )
    SELECT DISTINCT
           r.ingredient_id,
           i.name,
           ROUND(r.earliest, 6),
           ROUND(r.latest, 6),
           ROUND(100 * (r.latest - r.earliest) / NULLIF(r.earliest, 0), 1)
    FROM   ranked r
    JOIN   ingredient i ON i.id = r.ingredient_id
    WHERE  ABS(r.latest - r.earliest) / NULLIF(r.earliest, 0) > 0.03
    ORDER BY 5 DESC;
$$ LANGUAGE SQL STABLE;


-- =====================================================================
--  PIÈGES À TRAITER ENSUITE (volontairement hors V1)
--
--  * Trigger anti-cycle à l'INSERT sur recipe_component : le CHECK ne
--    couvre que l'auto-référence directe (A -> A), pas A -> B -> A.
--  * Versionner les recettes : si le pâtissier change son grammage,
--    l'historique de marge devient faux rétroactivement.
--  * Snapshot de coût daté : recipe_cost() donne le coût AUJOURD'HUI.
--    Pour un historique de marge, il faut figer un coût par période.
--  * Conversion g <-> ml quand ingredient.base_unit != component.unit :
--    à implémenter via density_g_per_ml (non fait ici volontairement,
--    c'est un bon exercice).
-- =====================================================================
