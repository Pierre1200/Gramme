-- =====================================================================
--  Seed de démonstration — CHIFFRES FICTIFS, calcul réel.
--
--  S'applique UNIQUEMENT à la base gramme_demo, recréée de zéro à chaque
--  `make demo-db`. Jamais à la base principale, où arriveront les vraies
--  factures : un seed qui écrit là finit par écraser des données clients.
--
--  Les prix sont inventés mais réalistes (mercuriales 2026). Les dates sont
--  relatives à CURRENT_DATE : price_drift ne regarde que les 42 derniers
--  jours, un seed daté en dur afficherait un rapport vide un mois plus tard.
--  Relancer `make demo` le jour de la démo.
-- =====================================================================

DO $$
BEGIN
    IF current_database() <> 'gramme_demo' THEN
        RAISE EXCEPTION 'seed de démo refusé sur la base « % » : réservé à gramme_demo',
                        current_database();
    END IF;
END $$;


-- ---------------------------------------------------------------------
--  Fournisseurs — noms génériques, marqués « exemple »
-- ---------------------------------------------------------------------
INSERT INTO supplier (id, name) VALUES
    (1, 'Épicerie & crèmerie (exemple)'),
    (2, 'Marée du port (exemple)'),
    (3, 'Boucherie des Halles (exemple)'),
    (4, 'Primeur du marché (exemple)'),
    (5, 'Torréfacteur (exemple)');


-- ---------------------------------------------------------------------
--  Ingrédients et produits fournisseurs (un produit par ingrédient)
-- ---------------------------------------------------------------------
INSERT INTO ingredient (id, name, base_unit) VALUES
    ( 1, 'Entrecôte de bœuf',        'g'),
    ( 2, 'Épaule d''agneau',         'g'),
    ( 3, 'Poulet, hauts de cuisse',  'g'),
    ( 4, 'Bar entier',               'g'),
    ( 5, 'Gambas entières',          'g'),
    ( 6, 'Moules',                   'g'),
    ( 7, 'Beurre doux 82 %',         'g'),
    ( 8, 'Crème liquide 35 %',       'ml'),
    ( 9, 'Lait entier',              'ml'),
    (10, 'Œufs entiers',             'unit'),
    (11, 'Farine T55',               'g'),
    (12, 'Sucre semoule',            'g'),
    (13, 'Levure fraîche',           'g'),
    (14, 'Riz bomba',                'g'),
    (15, 'Safran',                   'g'),
    (16, 'Huile d''olive',           'ml'),
    (17, 'Huile de friture',         'ml'),
    (18, 'Vin blanc de cuisine',     'ml'),
    (19, 'Café en grains',           'g'),
    (20, 'Pommes de terre',          'g'),
    (21, 'Citrons jaunes',           'g'),
    (22, 'Échalotes',                'g'),
    (23, 'Poivrons rouges',          'g'),
    (24, 'Tomates',                  'g'),
    (25, 'Oignons',                  'g'),
    (26, 'Carottes',                 'g'),
    (27, 'Ail',                      'g');

INSERT INTO supplier_product (id, supplier_id, raw_label, ingredient_id, status, mapped_by) VALUES
    ( 1, 3, 'ENTRECOTE BOEUF PAREE S/V',     1, 'confirmed', 'exemple'),
    ( 2, 3, 'EPAULE AGNEAU FR',              2, 'confirmed', 'exemple'),
    ( 3, 3, 'HT CUISSE POULET S/PEAU',       3, 'confirmed', 'exemple'),
    ( 4, 2, 'BAR LIGNE 1,5/2KG',             4, 'confirmed', 'exemple'),
    ( 5, 2, 'GAMBAS ENT. 20/30',             5, 'confirmed', 'exemple'),
    ( 6, 2, 'MOULES BOUCHOT',                6, 'confirmed', 'exemple'),
    ( 7, 1, 'BEURRE DOUX PLAQUE 82%',        7, 'confirmed', 'exemple'),
    ( 8, 1, 'CREME LIQ. 35% UHT 1L',         8, 'confirmed', 'exemple'),
    ( 9, 1, 'LAIT ENTIER UHT 1L',            9, 'confirmed', 'exemple'),
    (10, 1, 'OEUFS PLEIN AIR CAL.M X180',   10, 'confirmed', 'exemple'),
    (11, 1, 'FARINE T55 25KG',              11, 'confirmed', 'exemple'),
    (12, 1, 'SUCRE SEMOULE 10KG',           12, 'confirmed', 'exemple'),
    (13, 1, 'LEVURE FRAICHE 500G',          13, 'confirmed', 'exemple'),
    (14, 1, 'RIZ BOMBA 5KG',                14, 'confirmed', 'exemple'),
    (15, 1, 'SAFRAN PISTILS 5G',            15, 'confirmed', 'exemple'),
    (16, 1, 'HUILE OLIVE VIERGE 5L',        16, 'confirmed', 'exemple'),
    (17, 1, 'HUILE FRITURE 10L',            17, 'confirmed', 'exemple'),
    (18, 1, 'VIN BLANC CUISINE 5L',         18, 'confirmed', 'exemple'),
    (19, 5, 'CAFE GRAINS ESPRESSO 1KG',     19, 'confirmed', 'exemple'),
    (20, 4, 'POMME DE TERRE BINTJE 25KG',   20, 'confirmed', 'exemple'),
    (21, 4, 'CITRON JAUNE CAT.1',           21, 'confirmed', 'exemple'),
    (22, 4, 'ECHALOTE TRAD. 5KG',           22, 'confirmed', 'exemple'),
    (23, 4, 'POIVRON ROUGE 5KG',            23, 'confirmed', 'exemple'),
    (24, 4, 'TOMATE RONDE 6KG',             24, 'confirmed', 'exemple'),
    (25, 4, 'OIGNON JAUNE 10KG',            25, 'confirmed', 'exemple'),
    (26, 4, 'CAROTTE SABLE 10KG',           26, 'confirmed', 'exemple'),
    (27, 4, 'AIL BLANC 1KG',                27, 'confirmed', 'exemple');

-- Deux libellés que le système n'a pas su rattacher : ils alimentent la
-- section « à vérifier » du rapport, par la vraie requête.
INSERT INTO supplier_product (id, supplier_id, raw_label, ingredient_id, confidence, status, mapped_by) VALUES
    (101, 1, 'PREP. CITRON PASTEUR. 1L',  NULL, 0.54, 'pending', 'exemple'),
    (102, 4, 'MESCLUN JEUNES POUSSES 1KG', NULL, 0.61, 'pending', 'exemple');


-- ---------------------------------------------------------------------
--  Relevés de prix à J-42, J-21 et J-3
-- ---------------------------------------------------------------------
-- Écrits dans l'unité d'une mercuriale (€/kg, €/L, €/pièce) pour rester
-- relisibles, puis ramenés à l'unité de base par la division `par`.
WITH releves(produit, avant, milieu, apres, par) AS (VALUES
    -- Une semaine crédible : quelques hausses franches (gambas, beurre, huile
    -- d'olive), des produits stables, une baisse de saison. Un rapport où tout
    -- flambe paraîtrait exagéré et décrédibiliserait l'outil.
    ( 1,    26.00,    26.30,    26.60, 1000),   -- entrecôte        +2,3 % (sous le seuil)
    ( 2,    13.50,    13.50,    13.50, 1000),   -- épaule d'agneau  stable
    ( 3,     4.20,     4.20,     4.20, 1000),   -- poulet           stable
    ( 4,    18.50,    18.80,    19.00, 1000),   -- bar              +2,7 % (sous le seuil)
    ( 5,    14.00,    15.40,    16.80, 1000),   -- gambas          +20,0 %
    ( 6,     3.50,     3.50,     3.60, 1000),   -- moules           +2,9 % (sous le seuil)
    ( 7,     7.42,     8.10,     8.76, 1000),   -- beurre          +18,1 %
    ( 8,     3.10,     3.28,     3.44, 1000),   -- crème €/L       +11,0 %
    ( 9,     1.05,     1.05,     1.05, 1000),   -- lait €/L         stable
    (10,     0.21,     0.22,     0.23,    1),   -- œufs €/pièce     +9,5 %
    (11,     0.84,     0.85,     0.86, 1000),   -- farine           +2,4 % (sous le seuil)
    (12,     1.05,     1.05,     1.05, 1000),   -- sucre            stable
    (13,     5.00,     5.00,     5.00, 1000),   -- levure           stable
    (14,     4.50,     4.70,     4.86, 1000),   -- riz bomba        +8,0 %
    (15, 20000.00, 20000.00, 20000.00, 1000),   -- safran, 20 €/g   stable
    (16,     6.00,     6.60,     7.20, 1000),   -- huile d'olive   +20,0 %
    (17,     2.40,     2.40,     2.40, 1000),   -- huile friture    stable
    (18,     4.00,     4.00,     4.00, 1000),   -- vin blanc        stable
    (19,    22.00,    22.00,    22.00, 1000),   -- café             stable
    (20,     0.95,     0.95,     0.95, 1000),   -- pommes de terre  stable
    (21,     2.80,     2.80,     2.80, 1000),   -- citrons          stable
    (22,     3.20,     3.20,     3.20, 1000),   -- échalotes        stable
    (23,     2.80,     2.85,     2.90, 1000),   -- poivrons         +3,6 %
    (24,     2.20,     2.10,     2.02, 1000),   -- tomates          -8,2 % (fin de saison)
    (25,     1.50,     1.50,     1.50, 1000),   -- oignons          stable
    (26,     1.10,     1.10,     1.10, 1000),   -- carottes         stable
    (27,     6.00,     6.00,     6.00, 1000)    -- ail              stable
)
INSERT INTO price_point (supplier_product_id, observed_on, price_per_base_unit)
SELECT  r.produit,
        CURRENT_DATE - j.jours,
        CASE j.jours WHEN 42 THEN r.avant WHEN 21 THEN r.milieu ELSE r.apres END / r.par
FROM    releves r
CROSS JOIN (VALUES (42), (21), (3)) AS j(jours);


-- ---------------------------------------------------------------------
--  Recettes
-- ---------------------------------------------------------------------
-- selling_price_ttc = prix d'UNE portion. TVA 10 % sur place, 5,5 % en vitrine.
INSERT INTO recipe (id, name, yield_qty, yield_unit, is_sellable, portions, selling_price_ttc, vat_rate) VALUES
    -- Plats vendus
    ( 1, 'Filet de bar, beurre blanc',       1,     'unit', true,  1, 26.00, 0.100),
    ( 2, 'Entrecôte 300 g, frites',          1,     'unit', true,  1, 29.00, 0.100),
    ( 3, 'Épaule d''agneau confite 7 h',     1,     'unit', true,  1, 26.00, 0.100),
    ( 4, 'Paella mixte, la part',            25000, 'g',    true, 50, 12.00, 0.100),
    ( 5, 'Tarte citron meringuée, la part',  1,     'unit', true,  6,  4.50, 0.055),
    ( 6, 'Croissant pur beurre',             20,    'unit', true, 20,  1.40, 0.055),
    ( 7, 'Café expresso',                    1,     'unit', true,  1,  2.20, 0.100),
    -- Sous-recettes : une sous-recette EST une recette (is_sellable = false)
    (10, 'Beurre blanc',                     1000,  'g',    false, NULL, NULL, NULL),
    (11, 'Pâte sucrée',                      1000,  'g',    false, NULL, NULL, NULL),
    (12, 'Crème au citron',                  1000,  'g',    false, NULL, NULL, NULL),
    (13, 'Meringue italienne',               500,   'g',    false, NULL, NULL, NULL);

-- qty = quantité NETTE utilisée ; le coût se calcule sur le brut qty / yield_ratio.
INSERT INTO recipe_component (parent_recipe_id, ingredient_id, child_recipe_id, qty, unit, yield_ratio, note) VALUES
    -- Filet de bar, beurre blanc
    ( 1,    4, NULL,  140, 'g',    0.40, 'filets levés sur bar entier'),
    ( 1, NULL,   10,   60, 'g',    1.00, NULL),
    ( 1,   20, NULL,  150, 'g',    0.85, 'épluchées'),
    -- Entrecôte, frites
    ( 2,    1, NULL,  300, 'g',    0.88, 'parage de la pièce'),
    ( 2,   20, NULL,  250, 'g',    0.80, 'épluchées et taillées'),
    ( 2,   17, NULL,   30, 'ml',   1.00, 'absorption à la friture'),
    -- Épaule d'agneau confite
    ( 3,    2, NULL,  450, 'g',    0.75, 'désossée'),
    ( 3,   16, NULL,   20, 'ml',   1.00, NULL),
    ( 3,   26, NULL,   80, 'g',    0.85, NULL),
    ( 3,   27, NULL,   10, 'g',    1.00, NULL),
    -- Paella, batch de 50 parts
    ( 4,    5, NULL, 2250, 'g',    0.50, 'gambas décortiquées'),
    ( 4,   14, NULL, 5000, 'g',    1.00, NULL),
    ( 4,    3, NULL, 6000, 'g',    1.00, NULL),
    ( 4,    6, NULL, 5000, 'g',    1.00, 'servies en coquille'),
    ( 4,   15, NULL,    1, 'g',    1.00, NULL),
    ( 4,   23, NULL, 2000, 'g',    0.80, 'épépinés'),
    ( 4,   24, NULL, 2000, 'g',    1.00, NULL),
    ( 4,   25, NULL, 1500, 'g',    0.90, NULL),
    ( 4,   16, NULL,  300, 'ml',   1.00, NULL),
    -- Tarte citron meringuée, une tarte de 6 parts
    ( 5, NULL,   11,  400, 'g',    1.00, NULL),
    ( 5, NULL,   12,  700, 'g',    1.00, NULL),
    ( 5, NULL,   13,  250, 'g',    1.00, NULL),
    -- Croissants, une fournée de 20
    ( 6,   11, NULL, 1000, 'g',    1.00, NULL),
    ( 6,    7, NULL,  600, 'g',    1.00, 'détrempe et tourage'),
    ( 6,   12, NULL,  120, 'g',    1.00, NULL),
    ( 6,    9, NULL,  450, 'ml',   1.00, NULL),
    ( 6,   10, NULL,    2, 'unit', 1.00, 'dorure'),
    ( 6,   13, NULL,   30, 'g',    1.00, NULL),
    -- Café
    ( 7,   19, NULL,    8, 'g',    1.00, NULL),
    ( 7,   12, NULL,    5, 'g',    1.00, NULL),
    -- Beurre blanc
    (10,    7, NULL,  700, 'g',    1.00, NULL),
    (10,   22, NULL,  120, 'g',    0.90, 'épluchées'),
    (10,   18, NULL,  250, 'ml',   1.00, NULL),
    (10,    8, NULL,  100, 'ml',   1.00, NULL),
    -- Pâte sucrée
    (11,   11, NULL,  500, 'g',    1.00, NULL),
    (11,    7, NULL,  250, 'g',    1.00, NULL),
    (11,   12, NULL,  200, 'g',    1.00, NULL),
    (11,   10, NULL,    2, 'unit', 1.00, NULL),
    -- Crème au citron : seul le jus est compté (voir la note de fin)
    (12,   21, NULL,  300, 'g',    0.35, 'jus'),
    (12,   12, NULL,  250, 'g',    1.00, NULL),
    (12,   10, NULL,    5, 'unit', 1.00, NULL),
    (12,    7, NULL,  150, 'g',    1.00, NULL),
    -- Meringue italienne
    (13,   12, NULL,  350, 'g',    1.00, NULL),
    (13,   10, NULL,    4, 'unit', 1.00, 'blancs');


-- ---------------------------------------------------------------------
--  Une facture dont le total ne tombe pas juste (écart de 4,20 €)
-- ---------------------------------------------------------------------
INSERT INTO invoice (id, supplier_id, invoice_number, invoice_date, total_ht, source_uri, source_hash) VALUES
    (1, 2, 'MP-0418', CURRENT_DATE - 3, 312.20,
     'seed://demo/maree-du-port-0418', 'seed-demo-0418');

INSERT INTO invoice_line (invoice_id, line_no, raw_label, supplier_product_id, qty, unit_price_ht, line_total_ht) VALUES
    (1, 1, 'BAR LIGNE 1,5/2KG', 4, 12.5, 19.60, 245.00),
    (1, 2, 'MOULES BOUCHOT',    6, 17.5,  3.60,  63.00);


-- ---------------------------------------------------------------------
--  Réaligner les séquences après les id explicites
-- ---------------------------------------------------------------------
SELECT setval(pg_get_serial_sequence('supplier',         'id'), (SELECT max(id) FROM supplier));
SELECT setval(pg_get_serial_sequence('ingredient',       'id'), (SELECT max(id) FROM ingredient));
SELECT setval(pg_get_serial_sequence('supplier_product', 'id'), (SELECT max(id) FROM supplier_product));
SELECT setval(pg_get_serial_sequence('recipe',           'id'), (SELECT max(id) FROM recipe));
SELECT setval(pg_get_serial_sequence('invoice',          'id'), (SELECT max(id) FROM invoice));

-- NOTE — le citron de la crème ne compte que son jus. Compter aussi le zeste
-- comme un second composant ferait payer deux fois les mêmes citrons : c'est
-- le même problème de coproduits que la découpe de carcasse (CLAUDE.md).
