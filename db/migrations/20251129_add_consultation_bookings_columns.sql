-- Align consultation_bookings schema with application model
-- Adds columns that exist locally (SQLite) but are missing in Azure MySQL.

ALTER TABLE consultation_bookings
    ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(36) NULL,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NULL,
    ADD COLUMN IF NOT EXISTS meeting_url VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS line_contact VARCHAR(255) NULL;
