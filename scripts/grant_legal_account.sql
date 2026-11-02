-- Execute as a DBA account with GRANT OPTION on the authorized MySQL server.
-- Applies only to the newly created legal-project schema and account.
-- Never grant privileges on smart_home or smart_home_checkpoint.
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
ON `legal_assistant`.* TO 'legal_892cc80a'@'%';
