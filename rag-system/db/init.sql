CREATE TABLE IF NOT EXISTS users (
    id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    full_name       VARCHAR(255),
    hashed_password VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
    ON users(email);

CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename       VARCHAR(500) NOT NULL,
    original_name  VARCHAR(500) NOT NULL,
    object_key     VARCHAR(500) NOT NULL,
    file_type      VARCHAR(50),
    file_size      BIGINT,
    source_type    VARCHAR(20) NOT NULL DEFAULT 'file',
    source_uri     VARCHAR(2000),
    status         VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message  TEXT,
    chunk_count    INTEGER NOT NULL DEFAULT 0,
    ingest_task_id VARCHAR(255),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents(status);

CREATE INDEX IF NOT EXISTS idx_documents_source_type
    ON documents(source_type);

CREATE TABLE IF NOT EXISTS system_config (
    id            INTEGER PRIMARY KEY,
    chunk_size    INTEGER NOT NULL,
    chunk_overlap INTEGER NOT NULL,
    top_k         INTEGER NOT NULL,
    llm_model     VARCHAR(120) NOT NULL,
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO system_config (id, chunk_size, chunk_overlap, top_k, llm_model, updated_at)
VALUES (1, 400, 100, 3, 'gemini-2.5-flash', NOW())
ON CONFLICT (id) DO NOTHING;
