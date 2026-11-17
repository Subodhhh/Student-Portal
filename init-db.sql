-- init-db.sql

-- Create User for the Institute Portal
CREATE USER institute_app WITH PASSWORD 'institute_secure_password';

-- Create User for the Student Portal
CREATE USER student_app WITH PASSWORD 'student_secure_password';

-- Create the Database for the Institute Portal
CREATE DATABASE institute_db OWNER institute_app;

-- Create the Database for the Student Portal
CREATE DATABASE student_db OWNER student_app;

-- Grant privileges (Postgres 15+ requires explicit schema permissions)
\c institute_db
GRANT ALL ON SCHEMA public TO institute_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO institute_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO institute_app;

\c student_db
GRANT ALL ON SCHEMA public TO student_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO student_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO student_app;