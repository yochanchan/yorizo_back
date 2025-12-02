-- Ensure utf8mb4 for user-generated text columns.
ALTER TABLE conversations
  MODIFY main_concern TEXT
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

ALTER TABLE messages
  MODIFY content TEXT
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

ALTER TABLE consultation_memos
  MODIFY current_points TEXT
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci,
  MODIFY important_points TEXT
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

ALTER TABLE documents
  MODIFY filename VARCHAR(255)
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci,
  MODIFY content_text TEXT
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
