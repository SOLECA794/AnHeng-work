-- Apply explicitly after review. No grants are inferred or backfilled.
BEGIN;
-- The current B scrypt encoding is 116 characters; do not truncate or rehash.
ALTER TABLE sys_user ALTER COLUMN password TYPE VARCHAR(255);
CREATE TABLE IF NOT EXISTS d_scope_grant (
    user_id BIGINT NOT NULL REFERENCES sys_user(id) ON DELETE CASCADE,
    scope_id BIGINT NOT NULL REFERENCES auth_scope(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, scope_id)
);
CREATE INDEX IF NOT EXISTS idx_d_scope_grant_scope ON d_scope_grant (scope_id);
COMMIT;
