-- 0003: manual import is a first-class verification state.
--
-- A hand-entered figure from a record the operator is entitled to use is not
-- "unverified"; its provenance is a named person and an evidence URL. It is
-- also not an API. Conflating it with either hides what is actually known
-- about where a number came from.

BEGIN;

ALTER TYPE verification_status ADD VALUE IF NOT EXISTS 'manual_import';

COMMIT;
