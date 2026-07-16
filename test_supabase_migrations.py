import unittest
from pathlib import Path


class SupabaseMigrationTests(unittest.TestCase):
    def test_user_library_migration_has_rls_and_safe_auth_policy(self) -> None:
        base_migration = Path("supabase/migrations/20260521104159_user_accounts_library.sql")
        rls_migration = Path("supabase/migrations/20260521104419_optimize_rls_auth_uid.sql")
        self.assertTrue(base_migration.exists(), "Supabase user library migration is missing.")
        self.assertTrue(rls_migration.exists(), "Supabase RLS optimization migration is missing.")
        optimized_rls_sql = rls_migration.read_text(encoding="utf-8").lower()
        sql = "\n".join(
            [
                base_migration.read_text(encoding="utf-8").lower(),
                optimized_rls_sql,
            ]
        )

        for table in ("user_profiles", "conversion_jobs", "conversion_artifacts"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn(f"(select auth.uid()) = user_id", sql)

        self.assertIn("kindlemaster-artifacts", sql)
        self.assertIn("storage.objects", sql)
        self.assertNotIn("auth.uid() = user_id", optimized_rls_sql)
        self.assertNotIn("auth.uid()::text", optimized_rls_sql)
        self.assertNotIn("user_metadata", sql)
        self.assertNotIn("raw_user_meta_data", sql)
        self.assertNotIn("security definer", sql)

    def test_fen_review_migration_is_queryable_and_backend_only(self) -> None:
        migration = Path("supabase/migrations/20260716135104_chess_fen_review_database.sql")
        self.assertTrue(migration.exists(), "Supabase FEN review migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        for table in ("chess_fen_review_sessions", "chess_fen_review_labels"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn(f"revoke all on table public.{table} from anon, authenticated", sql)

        self.assertIn("row_payload jsonb not null", sql)
        self.assertIn("jsonb_array_length(square_labels) = 64", sql)
        self.assertIn("security invoker", sql)
        self.assertIn("grant execute on function public.save_chess_fen_review", sql)
        self.assertNotIn("security definer", sql)


if __name__ == "__main__":
    unittest.main()
