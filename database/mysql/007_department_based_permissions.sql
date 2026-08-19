-- Replace runtime role assignment with department-based administration.
-- The legacy role tables remain for upgrade compatibility but are no longer used.

SET NAMES utf8mb4;

ALTER TABLE app_user
    ADD COLUMN deleted_at DATETIME(3) NULL AFTER last_login_at,
    ADD KEY idx_app_user_deleted_status (deleted_at,status);

INSERT INTO department (code,name,parent_id)
SELECT 'PLATFORM_ADMIN','平台管理员',id
FROM department
WHERE code='COMPANY'
  AND NOT EXISTS (SELECT 1 FROM department WHERE code='PLATFORM_ADMIN');

UPDATE department admin_department
JOIN department company ON company.code='COMPANY'
SET admin_department.name='平台管理员',
    admin_department.parent_id=company.id,
    admin_department.status=1
WHERE admin_department.code='PLATFORM_ADMIN';

DELETE ud
FROM user_department ud
JOIN app_user u ON u.id=ud.user_id
WHERE u.username='admin';

INSERT INTO user_department (user_id,department_id,is_primary)
SELECT u.id,d.id,1
FROM app_user u
JOIN department d ON d.code='PLATFORM_ADMIN'
WHERE u.username='admin'
ON DUPLICATE KEY UPDATE is_primary=1;
