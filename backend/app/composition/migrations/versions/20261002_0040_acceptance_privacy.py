"""Add immutable acceptance sets, protected evaluation capture and scope validation."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision = "20261002_0040"
down_revision = "20261002_0039"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("index_acceptance_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("build_id", sa.Uuid(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "build_id"],
                                ["index_builds.project_id", "index_builds.id"], ondelete="CASCADE"))
    op.create_index("ix_index_acceptance_sets_project_build", "index_acceptance_sets",
                    ["project_id", "build_id"], unique=True)
    op.execute("""CREATE TRIGGER index_acceptance_set_immutable BEFORE UPDATE ON index_acceptance_sets
        FOR EACH ROW EXECUTE FUNCTION reject_index_acceptance_update()""")
    op.create_table("evaluation_diagnostics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(128), nullable=False),
        sa.Column("profile", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"))
    op.create_index("ix_evaluation_diagnostics_project_run", "evaluation_diagnostics", ["project_id", "run_id"])
    op.create_index("ix_evaluation_diagnostics_expiry", "evaluation_diagnostics", ["expires_at"])
    op.execute(SCOPE_VALIDATOR_SQL)

def downgrade() -> None:
    op.execute("DROP FUNCTION valid_source_scope_envelope(jsonb, text)")
    op.drop_table("evaluation_diagnostics")
    op.drop_table("index_acceptance_sets")

SCOPE_VALIDATOR_SQL = r"""
CREATE FUNCTION valid_source_scope_envelope(metadata jsonb, content text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE fact jsonb; span jsonb; review jsonb; quote text;
BEGIN
    IF metadata->>'scope_fact_version' IS DISTINCT FROM 'scope.v2'
       OR jsonb_typeof(metadata->'scope_facts') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    FOR fact IN SELECT value FROM jsonb_array_elements(metadata->'scope_facts') LOOP
        IF jsonb_typeof(fact) IS DISTINCT FROM 'object'
           OR fact->>'version' IS DISTINCT FROM 'scope.v2'
           OR COALESCE(fact->>'kind','') NOT IN ('period','provision')
           OR COALESCE(fact->>'scope','') NOT IN ('mention','governing')
           OR COALESCE(fact->>'locality','') NOT IN ('document','provision','table','span')
           OR COALESCE(fact->>'effect','') NOT IN ('unknown','operative','proposal','example')
           OR COALESCE(fact->>'status','') NOT IN ('source_attested','reviewed')
           OR COALESCE(fact->>'period_mode','') NOT IN ('single','range')
           OR jsonb_typeof(fact->'value') IS DISTINCT FROM 'string'
           OR jsonb_typeof(fact->'locality_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(fact->'exhaustive') IS DISTINCT FROM 'boolean'
           OR NOT (fact ?& ARRAY['version','kind','value','legal_kind','start_year','end_year',
                 'period_mode','scope','locality','locality_id','effect','exhaustive',
                 'source_span','status','review_provenance'])
           OR jsonb_typeof(fact->'source_span') IS DISTINCT FROM 'object'
           OR jsonb_typeof(fact->'review_provenance') IS DISTINCT FROM 'object'
           OR fact - ARRAY['version','kind','value','legal_kind','start_year','end_year',
                 'period_mode','scope','locality','locality_id','effect','exhaustive',
                 'source_span','status','review_provenance'] <> '{}'::jsonb THEN RETURN false; END IF;
        span := fact->'source_span'; review := fact->'review_provenance'; quote := span->>'text';
        IF jsonb_typeof(span->'text') IS DISTINCT FROM 'string' OR quote IS NULL
           OR quote = '' OR position(quote in content) = 0 THEN RETURN false; END IF;
        IF EXISTS (SELECT 1 FROM jsonb_each(review) WHERE jsonb_typeof(value) <> 'string')
            THEN RETURN false; END IF;
        IF fact->>'legal_kind' IS NOT NULL THEN
            IF fact->>'legal_kind' NOT IN ('assessment','fiscal','calendar')
               OR jsonb_typeof(fact->'start_year') IS DISTINCT FROM 'number'
               OR jsonb_typeof(fact->'end_year') IS DISTINCT FROM 'number'
               OR COALESCE(fact->>'start_year','') !~ '^[0-9]+$'
               OR COALESCE(fact->>'end_year','') !~ '^[0-9]+$'
               OR (fact->>'start_year')::bigint > (fact->>'end_year')::bigint
               THEN RETURN false; END IF;
        END IF;
        IF fact->>'scope' = 'governing' OR fact->>'effect' = 'operative'
           OR fact->>'exhaustive' = 'true' THEN
            IF fact->>'status' IS DISTINCT FROM 'reviewed'
               OR COALESCE(btrim(review->>'reviewer'),'') = ''
               OR COALESCE(btrim(review->>'reason'),'') = ''
               OR review->>'evidence_hash' IS DISTINCT FROM
                    encode(sha256(convert_to(quote,'UTF8')),'hex')
               OR span->>'provenance' IS DISTINCT FROM 'exact_source_span'
               OR jsonb_typeof(span->'char_start') IS DISTINCT FROM 'number'
               OR jsonb_typeof(span->'char_end') IS DISTINCT FROM 'number'
               OR span->>'char_start' !~ '^[0-9]+$' OR span->>'char_end' !~ '^[0-9]+$'
               OR (span->>'char_end')::bigint - (span->>'char_start')::bigint <> length(quote)
               THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$
"""
