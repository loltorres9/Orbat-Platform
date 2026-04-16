-- 001_drop_legacy_sheet_columns.sql
-- Removes old Google-Sheets-era columns after DB-native migration.

BEGIN;

ALTER TABLE operations DROP COLUMN IF EXISTS sheet_url;
ALTER TABLE operations DROP COLUMN IF EXISTS sheet_id;
ALTER TABLE operations DROP COLUMN IF EXISTS squad_col;
ALTER TABLE operations DROP COLUMN IF EXISTS role_col;
ALTER TABLE operations DROP COLUMN IF EXISTS status_col;
ALTER TABLE operations DROP COLUMN IF EXISTS assigned_col;

ALTER TABLE requests DROP COLUMN IF EXISTS sheet_row;
ALTER TABLE requests DROP COLUMN IF EXISTS sheet_col;

COMMIT;
