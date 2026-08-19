-- Repair the original company seed encoding and add the technical department pilot space.
-- Safe to run repeatedly.

SET NAMES utf8mb4;

UPDATE department
SET name=CONVERT(0xE585ACE58FB8 USING utf8mb4)
WHERE code='COMPANY';

INSERT INTO department (code,name,parent_id)
SELECT 'TECH','技术部',1
WHERE NOT EXISTS (SELECT 1 FROM department WHERE code='TECH');

UPDATE department SET name='技术部',parent_id=1,status=1 WHERE code='TECH';

INSERT INTO knowledge_base (code,name,description,owner_department_id,security_level,status)
SELECT 'TECH_KB','技术部知识库','技术规范、项目资料与图纸等技术部资料',id,'internal','active'
FROM department
WHERE code='TECH'
  AND NOT EXISTS (SELECT 1 FROM knowledge_base WHERE code='TECH_KB');

UPDATE knowledge_base k
JOIN department d ON d.code='TECH'
SET k.name='技术部知识库',
    k.description='技术规范、项目资料与图纸等技术部资料',
    k.owner_department_id=d.id,
    k.status='active'
WHERE k.code='TECH_KB';

INSERT INTO knowledge_base_department_acl (knowledge_base_id,department_id,permission)
SELECT k.id,d.id,'manage'
FROM knowledge_base k
JOIN department d ON d.code='TECH'
WHERE k.code='TECH_KB'
ON DUPLICATE KEY UPDATE permission=VALUES(permission);
