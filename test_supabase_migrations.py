import unittest
from pathlib import Path


class SupabaseMigrationTests(unittest.TestCase):
    def test_user_library_migration_has_rls_and_safe_auth_policy(self) -> None:
        migration = Path("supabase/migrations/202605210001_user_accounts_library.sql")
        self.assertTrue(migration.exists(), "Supabase user library migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        for table in ("user_profiles", "conversion_jobs", "conversion_artifacts"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn(f"(select auth.uid()) = user_id", sql)

        self.assertIn("kindlemaster-artifacts", sql)
        self.assertIn("storage.objects", sql)
        self.assertNotIn("auth.uid() = user_id", sql)
        self.assertNotIn("auth.uid()::text", sql)
        self.assertNotIn("user_metadata", sql)
        self.assertNotIn("raw_user_meta_data", sql)
        self.assertNotIn("security definer", sql)


if __name__ == "__main__":
    unittest.main()
