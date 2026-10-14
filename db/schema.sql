-- AnHeng work: PostgreSQL 16 schema, generated from src/models.
-- Idempotent: safe to run repeatedly in a development database.
-- Finding types intentionally allow MALICIOUS_LINK and MALICIOUS_SCRIPT.
BEGIN;


CREATE TABLE IF NOT EXISTS auth_scope (
	id BIGSERIAL NOT NULL, 
	domain VARCHAR(100) NOT NULL, 
	scope_type VARCHAR(20) NOT NULL, 
	scope_value VARCHAR(500), 
	auth_note VARCHAR(500), 
	owner VARCHAR(50), 
	status VARCHAR(20) NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	deleted INTEGER DEFAULT '0' NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (domain)
);


CREATE TABLE IF NOT EXISTS sensitive_rule (
	id BIGSERIAL NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	rule_type VARCHAR(20) NOT NULL, 
	pattern TEXT NOT NULL, 
	sample_demo TEXT, 
	scope_bind VARCHAR(100), 
	severity VARCHAR(10) NOT NULL, 
	enabled INTEGER DEFAULT '1' NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);


CREATE TABLE IF NOT EXISTS sys_user (
	id BIGSERIAL NOT NULL, 
	username VARCHAR(50) NOT NULL, 
	password VARCHAR(100) NOT NULL, 
	nickname VARCHAR(50), 
	role VARCHAR(20) NOT NULL, 
	status INTEGER DEFAULT '1' NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	deleted INTEGER DEFAULT '0' NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);


CREATE TABLE IF NOT EXISTS report (
	id BIGSERIAL NOT NULL, 
	scope_id BIGINT NOT NULL, 
	period_begin DATE NOT NULL, 
	period_end DATE NOT NULL, 
	report_type VARCHAR(20) NOT NULL, 
	file_path VARCHAR(255), 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(scope_id) REFERENCES auth_scope (id)
);


CREATE TABLE IF NOT EXISTS schedule (
	id BIGSERIAL NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	scope_id BIGINT NOT NULL, 
	cron VARCHAR(100), 
	interval_type VARCHAR(20), 
	execute_window VARCHAR(50), 
	enabled INTEGER DEFAULT '1' NOT NULL, 
	last_run_at TIMESTAMP WITHOUT TIME ZONE, 
	next_run_at TIMESTAMP WITHOUT TIME ZONE, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(scope_id) REFERENCES auth_scope (id)
);


CREATE TABLE IF NOT EXISTS site_page (
	id BIGSERIAL NOT NULL, 
	scope_id BIGINT NOT NULL, 
	url VARCHAR(500) NOT NULL, 
	normalized_url VARCHAR(500) NOT NULL, 
	title VARCHAR(200), 
	content_hash VARCHAR(64), 
	depth INTEGER DEFAULT '0' NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	first_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(scope_id) REFERENCES auth_scope (id), 
	UNIQUE (normalized_url)
);


CREATE TABLE IF NOT EXISTS baseline (
	id BIGSERIAL NOT NULL, 
	page_id BIGINT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_baseline_page UNIQUE (page_id), 
	FOREIGN KEY(page_id) REFERENCES site_page (id)
);


CREATE TABLE IF NOT EXISTS detection_task (
	id BIGSERIAL NOT NULL, 
	schedule_id BIGINT, 
	scope_id BIGINT NOT NULL, 
	trigger_type VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	retry_count INTEGER DEFAULT '0' NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE, 
	finished_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(schedule_id) REFERENCES schedule (id), 
	FOREIGN KEY(scope_id) REFERENCES auth_scope (id)
);


CREATE TABLE IF NOT EXISTS agent_log (
	id BIGSERIAL NOT NULL, 
	user_id BIGINT, 
	task_id BIGINT, 
	log_type VARCHAR(20) NOT NULL, 
	tool_name VARCHAR(50), 
	action VARCHAR(100) NOT NULL, 
	params_summary VARCHAR(500), 
	result_status VARCHAR(20) NOT NULL, 
	latency_ms INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES sys_user (id), 
	FOREIGN KEY(task_id) REFERENCES detection_task (id)
);


CREATE TABLE IF NOT EXISTS finding (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	page_id BIGINT, 
	scope_id BIGINT NOT NULL, 
	finding_type VARCHAR(30) NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	url VARCHAR(500), 
	evidence TEXT, 
	impact TEXT, 
	suggestion TEXT, 
	tool_name VARCHAR(50) NOT NULL, 
	raw_data JSONB, 
	dedup_key VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	occurrence INTEGER DEFAULT '1' NOT NULL, 
	first_seen TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	last_seen TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uk_finding_dedup UNIQUE (task_id, dedup_key), 
	FOREIGN KEY(task_id) REFERENCES detection_task (id), 
	FOREIGN KEY(page_id) REFERENCES site_page (id), 
	FOREIGN KEY(scope_id) REFERENCES auth_scope (id)
);


CREATE TABLE IF NOT EXISTS alert (
	id BIGSERIAL NOT NULL, 
	finding_id BIGINT NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	content VARCHAR(500), 
	status VARCHAR(20) NOT NULL, 
	read_status INTEGER DEFAULT '0' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(finding_id) REFERENCES finding (id)
);

CREATE INDEX IF NOT EXISTS idx_scope_status ON auth_scope (status);

CREATE INDEX IF NOT EXISTS idx_rule_type ON sensitive_rule (rule_type, enabled);

CREATE INDEX IF NOT EXISTS idx_user_role ON sys_user (role);

CREATE INDEX IF NOT EXISTS idx_report_scope ON report (scope_id);

CREATE INDEX IF NOT EXISTS idx_schedule_scope ON schedule (scope_id, enabled);

CREATE INDEX IF NOT EXISTS idx_page_scope ON site_page (scope_id);

CREATE INDEX IF NOT EXISTS idx_page_status ON site_page (status);

CREATE INDEX IF NOT EXISTS idx_baseline_page ON baseline (page_id);

CREATE INDEX IF NOT EXISTS idx_task_scope ON detection_task (scope_id, status);

CREATE INDEX IF NOT EXISTS idx_task_time ON detection_task (created_at);

CREATE INDEX IF NOT EXISTS idx_log_time ON agent_log (created_at);

CREATE INDEX IF NOT EXISTS idx_log_type ON agent_log (log_type);

CREATE INDEX IF NOT EXISTS idx_log_user ON agent_log (user_id);

CREATE INDEX IF NOT EXISTS idx_finding_scope ON finding (scope_id);

CREATE INDEX IF NOT EXISTS idx_finding_status ON finding (status);

CREATE INDEX IF NOT EXISTS idx_finding_type ON finding (finding_type, severity);

CREATE INDEX IF NOT EXISTS idx_alert_finding ON alert (finding_id);

CREATE INDEX IF NOT EXISTS idx_alert_status ON alert (status);

COMMIT;
