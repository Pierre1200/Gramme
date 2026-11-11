-- =====================================================================
--  Ce qu'une facture lue porte, et que 001_schema.sql ne savait pas garder.
--
--  Écrit pour l'écriture en base des factures électroniques (Factur-X) :
--  unité et TVA de chaque ligne, total TTC, remises et frais de pied de
--  facture, SIREN du fournisseur. Rien ici ne calcule de prix : le
--  price_point naît à la normalisation, quand le contenu d'un colis est connu.
-- =====================================================================


-- Un fournisseur se reconnaît à son SIREN, pas à son nom : la raison sociale
-- change de présentation d'une facture à l'autre, le SIREN jamais. Vide pour
-- une facture papier qui ne le porte pas, et pour le seed de démonstration.
ALTER TABLE supplier
    ADD COLUMN siren TEXT UNIQUE CHECK (siren ~ '^[0-9]{9}$');


-- Lu pour vérifier que le HT est bien le HT : confondre les deux gonfle tous
-- les coûts de 5,5 à 20 %.
ALTER TABLE invoice
    ADD COLUMN total_ttc NUMERIC(12,2);


-- Ferme le piège « unité de la ligne » de 001_schema.sql : sans elle,
-- « 2 CT » et « 12,5 KG » sont indiscernables, et tout prix au gramme en
-- dépend. Unité telle que lue (code de la norme pour un Factur-X) ; sa
-- conversion en unité de base appartient à la normalisation.
--
-- Taux de TVA en pourcentage (5.50, 20.00) : un client en franchise de TVA
-- ne la récupère pas, son coût réel est le TTC de chaque ligne.
ALTER TABLE invoice_line
    ADD COLUMN raw_unit TEXT,
    ADD COLUMN vat_rate NUMERIC(5,2);


-- Remises et frais de pied de facture. Sans eux, la somme des lignes ne
-- retombe pas sur le total et une facture complète passe pour tronquée.
-- Montant signé : négatif pour une remise, positif pour des frais.
CREATE TABLE invoice_adjustment (
    id          BIGSERIAL PRIMARY KEY,
    invoice_id  BIGINT NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    amount_ht   NUMERIC(12,2) NOT NULL
);

CREATE INDEX ON invoice_adjustment (invoice_id);


-- Un produit sans référence article (petit fournisseur) se reconnaît à son
-- libellé. La contrainte UNIQUE (supplier_id, supplier_ref) ne le protège pas :
-- deux valeurs vides ne sont jamais égales pour Postgres, et chaque facture
-- créerait un nouveau produit.
CREATE UNIQUE INDEX supplier_product_label_when_no_ref
    ON supplier_product (supplier_id, raw_label)
    WHERE supplier_ref IS NULL;


-- Le contrôle des totaux compte désormais le pied de facture. Même colonnes
-- que dans 002_rapport.sql, donc les vues qui en dépendent restent valides.
-- Tolérance 0,05 € : la même que INVOICE_TOTAL_TOLERANCE dans models.py.
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
  AND   ABS(t.counted - i.total_ht) > 0.05;
