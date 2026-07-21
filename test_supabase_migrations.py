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
            self.assertIn("(select auth.uid()) = user_id", sql)

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

    def test_fen_review_gold_contract_backfill_is_explicit(self) -> None:
        migration = Path("supabase/migrations/20260716194751_chess_fen_review_gold_contract.sql")
        self.assertTrue(migration.exists(), "Supabase FEN gold-contract migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        self.assertIn("update public.chess_fen_review_labels", sql)
        self.assertIn("'verification_source', 'human_visual'", sql)
        self.assertIn("'square_diff_ack', true", sql)
        self.assertIn("'human_verified', true", sql)
        self.assertIn("label_status = 'verified'", sql)
        self.assertIn("piece_labels_verified is true", sql)

    def test_fen_review_versioning_is_revision_guarded_and_owner_scoped(self) -> None:
        migration = Path("supabase/migrations/20260720095704_version_chess_fen_review_labels.sql")
        self.assertTrue(migration.exists(), "Supabase FEN review versioning migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        for table in ("chess_fen_review_label_history", "chess_fen_dataset_versions"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
        self.assertIn("fen_review_revision_conflict", sql)
        self.assertIn("fen_review_close_requires_complete_valid_rows", sql)
        self.assertIn("previous_payload jsonb", sql)
        self.assertIn("new_payload jsonb not null", sql)
        self.assertIn("(select auth.uid()) = owner_user_id", sql)
        self.assertIn("p_expected_revision bigint", sql)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("security invoker", sql)
        self.assertNotIn("security definer", sql)

    def test_fen_review_owner_foreign_keys_are_indexed(self) -> None:
        migration = Path("supabase/migrations/20260720095829_index_chess_fen_review_ownership.sql")
        self.assertTrue(migration.exists(), "Supabase FEN review ownership index migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        self.assertIn("chess_fen_review_sessions_owner_source_updated_idx", sql)
        self.assertIn("owner_user_id", sql)
        self.assertIn("source_document_sha256", sql)
        self.assertIn("chess_fen_review_sessions_closed_by_idx", sql)
        self.assertIn("closed_by_user_id", sql)
        self.assertIn("chess_fen_dataset_versions_owner_created_idx", sql)

    def test_fen_review_placement_status_closes_without_claiming_full_fen(self) -> None:
        migration = Path("supabase/migrations/20260721154000_fen_review_placement_verified.sql")
        self.assertTrue(migration.exists(), "Supabase placement-only FEN migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        self.assertIn("'placement_verified'", sql)
        self.assertIn("create or replace function public.close_chess_fen_review", sql)
        self.assertIn("item ->> 'label_status' = 'verified'", sql)
        self.assertIn("item ->> 'label_status' in ('verified', 'placement_verified')", sql)
        self.assertIn("public.save_chess_fen_review", sql)
        self.assertIn("insert into public.chess_fen_dataset_versions", sql)
        self.assertIn("security invoker", sql)
        self.assertNotIn("security definer", sql)

    def test_evidence_review_queue_is_backend_only_and_revision_guarded(self) -> None:
        migration = Path("supabase/migrations/20260717150334_chess_evidence_review_queue.sql")
        self.assertTrue(migration.exists(), "Supabase evidence review migration is missing.")
        sql = migration.read_text(encoding="utf-8").lower()

        for table in ("chess_evidence_review_sessions", "chess_evidence_review_items"):
            self.assertIn(f"create table if not exists public.{table}", sql)
            self.assertIn(f"alter table public.{table} enable row level security", sql)
            self.assertIn(f"revoke all on table public.{table} from anon, authenticated", sql)
        self.assertIn("evidence_review_revision_conflict", sql)
        self.assertIn("visible_marker_requires_bbox", sql)
        self.assertIn("marker_absence_requires_complete_crop", sql)
        self.assertIn("security invoker", sql)
        self.assertNotIn("security definer", sql)


if __name__ == "__main__":
    unittest.main()
