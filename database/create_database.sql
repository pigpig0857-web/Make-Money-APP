-- Run while connected to the existing postgres database.
-- Run outside a transaction because CREATE DATABASE requires it.
CREATE DATABASE "Make Money APP"
    WITH OWNER = postgres
    ENCODING = 'UTF8'
    TEMPLATE = template0;
