SET NAMES utf8mb4;

-- Preserve administrator-customized values; only retire the original defaults.
UPDATE agent
SET description = '统一识别员工意图，并在当前用户授权范围内调度知识、只读工具和 Skill。'
WHERE code = 'ENTERPRISE_ASSISTANT'
  AND description = '统一识别员工意图，并在当前用户授权范围内调度知识、只读工具、Skill 和专业智能体。';

UPDATE agent
SET system_prompt = '你是企业总助手。仅在当前用户授权范围内选择知识、只读工具或 Skill；保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。'
WHERE code = 'ENTERPRISE_ASSISTANT'
  AND system_prompt = '你是企业总助手。仅在当前用户授权范围内选择知识、只读工具、Skill 或专业智能体；保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。';
