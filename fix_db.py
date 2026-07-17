import os
import django

def check_and_fix_db():
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'django_migrations');")
            if not cursor.fetchone()[0]:
                return # Fresh DB, nothing to do
            
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'accounts_user');")
            if not cursor.fetchone()[0]:
                print("DETECTED CORRUPTED DB STATE! Migrations table exists but accounts_user does not. Wiping public schema to force a clean re-migration...")
                cursor.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
                print("Public schema wiped successfully. Ready for fresh migrations.")
    except Exception as e:
        print("Error checking/fixing DB:", e)

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    check_and_fix_db()
