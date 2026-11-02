"""Provision a NEW isolated database/account. Never alter existing schemas or accounts.

Only run against an explicitly authorized server. Sibling admin credentials stay in memory.
"""
import argparse
import secrets
from pathlib import Path

import pymysql
from dotenv import dotenv_values, set_key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-env', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.error('Explicit --execute required; creates a new database and restricted user.')
    root = Path(__file__).resolve().parents[1]
    source = dotenv_values(args.source_env, encoding='utf-8-sig')
    local = dotenv_values(root / '.env', encoding='utf-8-sig')
    admin, password = source.get('MYSQL_USER_ADMIN'), source.get('MYSQL_PASSWORD_ADMIN')
    if not admin or not password or not source.get('MYSQL_HOST'):
        raise SystemExit('Source lacks explicit admin/server configuration. No fallback account attempted.')
    db_name = local.get('MYSQL_DATABASE', 'legal_assistant')
    if not db_name.replace('_', '').isalnum() or db_name == source.get('MYSQL_DB'):
        raise SystemExit('Unsafe or shared database name. No changes made.')
    user = 'legal_' + secrets.token_hex(4)
    app_password = secrets.token_urlsafe(32)
    connection = pymysql.connect(host=source['MYSQL_HOST'], port=int(source.get('MYSQL_PORT', 3306)),
                                 user=admin, password=password, connect_timeout=5, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SHOW GRANTS')
            if not any('WITH GRANT OPTION' in row[0] for row in cursor.fetchall()):
                raise SystemExit('Admin has no GRANT OPTION. Ask DBA to provision/grant before making any changes.')
            cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (db_name,))
            if cursor.fetchone():
                raise SystemExit('Target database already exists; refusing to alter it. Choose a fresh MYSQL_DATABASE.')
            cursor.execute(f'CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            cursor.execute('CREATE USER %s@%s IDENTIFIED BY %s', (user, '%', app_password))
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES ON `{db_name}`.* TO %s@%s', (user, '%'))
    finally:
        connection.close()
    # Save only the NEW app account and this server address, never admin credentials.
    for key, value in {'MYSQL_HOST': source['MYSQL_HOST'], 'MYSQL_PORT': source.get('MYSQL_PORT', '3306'),
                       'MYSQL_DATABASE': db_name, 'MYSQL_USER': user, 'MYSQL_PASSWORD': app_password}.items():
        set_key(str(root / '.env'), key, value)
    print('Created isolated legal database and restricted app account; saved only new app settings in project .env.')


if __name__ == '__main__':
    try:
        main()
    except pymysql.MySQLError as error:
        print('MySQL provisioning failed; numeric error code:', error.args[0] if error.args else 'unknown')
        raise SystemExit(1)
