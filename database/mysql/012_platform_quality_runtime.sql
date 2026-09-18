SET NAMES utf8mb4;

ALTER TABLE agent ADD COLUMN config_version INT NOT NULL DEFAULT 1;
CREATE TABLE agent_revision (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 agent_id BIGINT UNSIGNED NOT NULL,
 version INT NOT NULL,
 snapshot_json JSON NOT NULL,
 created_by BIGINT UNSIGNED NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 UNIQUE KEY uk_agent_revision(agent_id,version),
 FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE chat_task (
 id CHAR(36) PRIMARY KEY,
 session_id CHAR(36) NOT NULL,
 agent_id BIGINT UNSIGNED NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 user_message_id BIGINT UNSIGNED NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 request_json JSON NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'queued',
 stage VARCHAR(64) NOT NULL DEFAULT '排队中',
 partial_answer MEDIUMTEXT NULL,
 result_json JSON NULL,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 error_code VARCHAR(128) NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 started_at DATETIME(3) NULL,
 finished_at DATETIME(3) NULL,
 UNIQUE KEY uk_task_request(user_id,request_key),
 KEY idx_task_queue(status,created_at),
 KEY idx_task_session(session_id,status),
 FOREIGN KEY(session_id) REFERENCES chat_session(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE evaluation_case (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 agent_id BIGINT UNSIGNED NOT NULL,
 question VARCHAR(4000) NOT NULL,
 expected_document_ids JSON NOT NULL,
 expect_no_evidence BOOLEAN NOT NULL DEFAULT FALSE,
 notes TEXT NULL,
 created_by BIGINT UNSIGNED NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 KEY idx_eval_agent(agent_id),
 FOREIGN KEY(agent_id) REFERENCES agent(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE evaluation_run (
 id CHAR(36) PRIMARY KEY,
 agent_id BIGINT UNSIGNED NOT NULL,
 created_by BIGINT UNSIGNED NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'queued',
 config_json JSON NOT NULL,
 cases_json JSON NOT NULL,
 results_json JSON NULL,
 metrics_json JSON NULL,
 error_code VARCHAR(128) NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 started_at DATETIME(3) NULL,
 finished_at DATETIME(3) NULL,
 KEY idx_eval_queue(status,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE answer_feedback (
 message_id BIGINT UNSIGNED NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 rating SMALLINT NOT NULL,
 comment VARCHAR(2000) NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 PRIMARY KEY(message_id,user_id),
 FOREIGN KEY(message_id) REFERENCES chat_message(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE content_unit ADD COLUMN parent_text MEDIUMTEXT NULL;
ALTER TABLE knowledge_base ADD COLUMN processing_config_json JSON NULL;
CREATE TABLE workflow_run (
 id CHAR(36) PRIMARY KEY,
 agent_id BIGINT UNSIGNED NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'queued',
 config_json JSON NOT NULL,
 input_json JSON NOT NULL,
 state_json JSON NOT NULL,
 error_code VARCHAR(128) NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 started_at DATETIME(3) NULL,
 finished_at DATETIME(3) NULL,
 KEY idx_workflow_queue(status,created_at),
 FOREIGN KEY(agent_id) REFERENCES agent(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
