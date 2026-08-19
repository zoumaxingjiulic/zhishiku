-- Hide legacy automated acceptance-test accounts left by earlier smoke tests.
-- This is a recoverable soft deletion and does not remove audit history.

UPDATE app_user
SET status=0,
    deleted_at=COALESCE(deleted_at,NOW(3))
WHERE external_id=CONCAT('local:',username)
  AND username REGEXP '^e2e_(hr|other|tech)_[0-9]+$';
