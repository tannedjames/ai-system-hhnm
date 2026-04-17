-- =============================================================================
-- RAG System — PostgreSQL Initialization Script
-- =============================================================================
-- This script runs automatically when the PostgreSQL container starts
-- for the first time (mounted as init.sql).
-- =============================================================================

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Placeholder tables (will be managed by Alembic later)
-- These are here just to verify the DB initializes properly.

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filename VARCHAR(512) NOT NULL,
    file_type VARCHAR(50),
    file_size_bytes BIGINT,
    minio_path TEXT,
    status VARCHAR(50) DEFAULT 'pending',  -- pending | processing | completed | failed
    chunk_count INTEGER DEFAULT 0,
    uploaded_by UUID REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Seed a default admin user (password: admin123 — change in production!)
-- BCrypt hash for "admin123"
INSERT INTO users (email, hashed_password, full_name, role)
VALUES (
    'admin@rag-system.local',
    '$2b$12$LJ3m4ys5Rn4s5E.example.hash.replace.in.production',
    'System Administrator',
    'admin'
) ON CONFLICT (email) DO NOTHING;
