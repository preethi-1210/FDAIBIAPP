-- Unity Catalog hardening for banking analytics. NOT YET APPLIED: review, then run as a metastore
-- or catalog administrator. The application currently connects with a personal owner account, so
-- only application-level controls protect these columns today.

-- 1. A service principal for the application, in place of a personal account.
--    Create `ryze-banking-analytics` in the account console, then use its token in DATABRICKS_TOKEN.

-- 2. Read access to the approved tables only. No access to bronze, silver, or other catalogs.
GRANT USE CATALOG ON CATALOG banking TO `ryze-banking-analytics`;
GRANT USE SCHEMA ON SCHEMA banking.gold TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_transactions TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_accounts TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_branches TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_cards TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_customers TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_loans TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.gold_loan_repayments TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_transactions_daily TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_transactions_monthly TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_accounts_daily TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_accounts_monthly TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_cards_daily TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_cards_monthly TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_loans_daily TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_loans_monthly TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_loan_repayments_daily TO `ryze-banking-analytics`;
GRANT SELECT ON TABLE banking.gold.agg_loan_repayments_monthly TO `ryze-banking-analytics`;
-- The app also reads schema metadata through information_schema, limited by these grants.

-- 3. Column masks, so identifiers stay hidden even if every application check failed. Join keys
--    keep working because the mask returns a stable hash rather than null.
CREATE FUNCTION IF NOT EXISTS banking.gold.mask_identifier(value STRING)
  RETURN CASE WHEN is_account_group_member('banking-data-admins') THEN value ELSE sha2(value, 256) END;
CREATE FUNCTION IF NOT EXISTS banking.gold.mask_personal(value STRING)
  RETURN CASE WHEN is_account_group_member('banking-data-admins') THEN value ELSE NULL END;
CREATE FUNCTION IF NOT EXISTS banking.gold.mask_personal_date(value DATE)
  RETURN CASE WHEN is_account_group_member('banking-data-admins') THEN value ELSE NULL END;
CREATE FUNCTION IF NOT EXISTS banking.gold.mask_personal_long(value BIGINT)
  RETURN CASE WHEN is_account_group_member('banking-data-admins') THEN value ELSE NULL END;

ALTER TABLE banking.gold.gold_transactions ALTER COLUMN counterparty_account SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_transactions ALTER COLUMN txn_id SET MASK banking.gold.mask_identifier;
ALTER TABLE banking.gold.gold_cards ALTER COLUMN card_id SET MASK banking.gold.mask_identifier;
ALTER TABLE banking.gold.gold_cards ALTER COLUMN expiry_date SET MASK banking.gold.mask_personal_date;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN first_name SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN last_name SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN email SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN pan_number SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN address SET MASK banking.gold.mask_personal;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN dob SET MASK banking.gold.mask_personal_date;
ALTER TABLE banking.gold.gold_customers ALTER COLUMN phone SET MASK banking.gold.mask_personal_long;

-- Masking account_id and customer_id with mask_identifier also works, since both sides of each join
-- hash the same way, but it must be applied to every table holding the key at once. Test on a copy.

-- 4. Verify from the service principal: each must fail or return masked values.
-- SELECT first_name FROM banking.gold.gold_customers LIMIT 1;
-- SELECT * FROM banking.silver.transactions LIMIT 1;
-- SELECT * FROM retail.gold.gold_orders LIMIT 1;
