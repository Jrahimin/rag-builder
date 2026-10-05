"""Every activation prerequisite is separate from structural/hash integrity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.index_acceptance import IndexAcceptance
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.modules.retrieval.build_acceptance import (
    AcceptanceCase,
    AcceptanceSetDefinition,
    BuildAcceptanceArtifact,
    digest,
    embedding_identity,
    require_build_acceptance,
    validate_acceptance,
)
from app.modules.retrieval.workflows.index_build_workflow import activate_index_build
from app.platform.jobs.errors import PermanentJobError

pytestmark = pytest.mark.unit


def fixture():
    build = IndexBuild(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        state=IndexBuildState.VALIDATED,
        operation=IndexBuildOperation.REINDEX,
        embedding_set_version=1,
        configuration_hash="c" * 64,
        corpus_fingerprint="d" * 64,
        validated_at=datetime.now(UTC),
        chunk_count=0,
        vector_count=0,
        keyword_count=0,
        manifest={
            "documents": [],
            "embedding_provider": "hash",
            "embedding_model": "hash-v1",
            "embedding_dimensions": 8,
            "embedding_set_version": 1,
        },
    )
    identity = {
        "project_id": build.project_id,
        "code": "a" * 64,
        "configuration_hash": "b" * 64,
        "structure_hash": "f" * 64,
        "source_generation": 7,
        "config_revision_id": uuid.uuid4(),
        "effective_llm": "echo",
    }
    case = AcceptanceCase(
        case_id="fixture",
        repetition=1,
        expected="insufficient_evidence",
        actual="insufficient_evidence",
        evidence_hash=digest({"observed": "empty"}),
        assertions_verified=True,
        scope_verified=True,
    )
    definition = AcceptanceSetDefinition(
        project_id=build.project_id,
        build_id=build.id,
        revision="fixture.v1",
        certification="offline_fixture",
        repetitions=1,
        require_comparison=False,
        case_expectations={"fixture": "insufficient_evidence"},
    )
    identity["acceptance_definition"] = definition
    artifact = BuildAcceptanceArtifact(
        certification="offline_fixture",
        project_id=build.project_id,
        build_id=build.id,
        code_fingerprint=identity["code"],
        configuration_hash=identity["configuration_hash"],
        project_config_revision_id=identity["config_revision_id"],
        index_configuration_hash=build.configuration_hash,
        source_generation=7,
        corpus_fingerprint=build.corpus_fingerprint,
        build_manifest_hash=digest(build.manifest),
        semantic_structure_hash="f" * 64,
        embedding_identity=embedding_identity(build),
        acceptance_set_revision="fixture.v1",
        acceptance_set_hash=digest(definition.model_dump(mode="json")),
        report_hash=digest([case.model_dump(mode="json")]),
        cases=[case],
    )
    return build, identity, artifact


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", uuid.uuid4()),
        ("build_id", uuid.uuid4()),
        ("code_fingerprint", "f" * 64),
        ("configuration_hash", "e" * 64),
        ("index_configuration_hash", "e" * 64),
        ("semantic_structure_hash", "e" * 64),
        ("project_config_revision_id", uuid.uuid4()),
        ("source_generation", 8),
        ("corpus_fingerprint", "a" * 64),
        ("build_manifest_hash", "b" * 64),
        ("embedding_identity", {"embedding_provider": "cohere"}),
    ],
)
def test_each_stale_identity_rejects_receipt(field, value):
    build, identity, artifact = fixture()
    with pytest.raises(PermanentJobError) as caught:
        validate_acceptance(artifact.model_copy(update={field: value}), build, **identity)
    assert caught.value.code == "index_acceptance_stale"


def test_report_failures_duplicates_and_paid_builds_cannot_be_offline_certified():
    build, identity, artifact = fixture()
    validate_acceptance(artifact, build, **identity)
    for actual in ("verification_failed", "timed_out"):
        cases = [artifact.cases[0].model_copy(update={"actual": actual})]
        invalid = artifact.model_copy(
            update={
                "cases": cases,
                "report_hash": digest([c.model_dump(mode="json") for c in cases]),
            }
        )
        with pytest.raises(PermanentJobError, match="failed/unverified"):
            validate_acceptance(invalid, build, **identity)
    with pytest.raises(PermanentJobError) as caught:
        validate_acceptance(artifact, build, **{**identity, "effective_llm": "openai"})
    assert caught.value.code == "index_acceptance_offline_only"
    production = artifact.model_copy(update={"certification": "production"})
    with pytest.raises(PermanentJobError) as caught:
        validate_acceptance(production, build, **identity)
    assert caught.value.code == "index_acceptance_incomplete"


@pytest.mark.parametrize("mode", ["missing", "stale", "valid"])
async def test_common_activation_gate_precedes_pointer_mutation(mode):
    build, identity, artifact = fixture()
    session = AsyncMock()
    rows = MagicMock()
    raw = artifact.model_dump(mode="json")
    if mode == "stale":
        raw["source_generation"] = 8
    record = IndexAcceptance(
        project_id=build.project_id,
        build_id=build.id,
        artifact=raw,
        artifact_hash=digest(raw),
        created_by="fixture",
    )
    rows.scalars.return_value.all.return_value = [] if mode == "missing" else [record]
    session.execute.return_value = rows
    from types import SimpleNamespace

    definition = identity["acceptance_definition"]
    with (
        patch(
            "app.modules.retrieval.repositories.index_acceptance_repository.IndexAcceptanceRepository.definition",
            AsyncMock(
                return_value=SimpleNamespace(
                    definition=definition.model_dump(mode="json"),
                    definition_hash=digest(definition.model_dump(mode="json")),
                )
            ),
        ),
        patch(
            "app.modules.retrieval.build_acceptance.current_acceptance_identity",
            AsyncMock(return_value=identity),
        ),
        patch(
            "app.modules.retrieval.build_acceptance.semantic_snapshot_hash",
            AsyncMock(return_value="f" * 64),
        ),
    ):
        if mode == "valid":
            await require_build_acceptance(session, build.project_id, build)
        else:
            with (
                patch(
                    "app.modules.retrieval.workflows.index_build_workflow.acquire_project_stage_lock",
                    AsyncMock(),
                ),
                pytest.raises(PermanentJobError) as caught,
            ):
                await activate_index_build(session, build.project_id, build)
            assert caught.value.code == (
                "index_acceptance_missing" if mode == "missing" else "index_acceptance_stale"
            )
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


def test_distinct_project_sets_reject_cross_project_case_substitution():
    first, identity, artifact = fixture()
    second, second_identity, second_artifact = fixture()
    # A different project owns its own reviewed acceptance question and revision.
    own = second_identity["acceptance_definition"].model_copy(
        update={
            "revision": "library-search.v2",
            "case_expectations": {"library-rule": "insufficient_evidence"},
        }
    )
    case = second_artifact.cases[0].model_copy(update={"case_id": "library-rule"})
    second_artifact = second_artifact.model_copy(
        update={
            "cases": [case],
            "report_hash": digest([case.model_dump(mode="json")]),
            "acceptance_set_revision": own.revision,
            "acceptance_set_hash": digest(own.model_dump(mode="json")),
        }
    )
    second_identity["acceptance_definition"] = own
    validate_acceptance(artifact, first, **identity)
    validate_acceptance(second_artifact, second, **second_identity)
    with pytest.raises(PermanentJobError):
        validate_acceptance(
            second_artifact,
            second,
            **{**second_identity, "acceptance_definition": identity["acceptance_definition"]},
        )
    with pytest.raises(PermanentJobError, match="approved project/build set"):
        validate_acceptance(
            second_artifact.model_copy(
                update={"acceptance_set_hash": artifact.acceptance_set_hash}
            ),
            second,
            **second_identity,
        )


def test_loaded_runtime_cannot_accept_receipt_for_changed_disk(tmp_path, monkeypatch):
    from app.platform.domain.runtime_identity import capture_code_identity, runtime_code_fingerprint

    source = tmp_path / "runner.py"
    source.write_text("CONTRACT = 'old'", encoding="utf-8")
    old = capture_code_identity(tmp_path)
    loaded = runtime_code_fingerprint()
    source.write_text("CONTRACT = 'new'", encoding="utf-8")
    new = capture_code_identity(tmp_path)
    assert old != new
    # The production runtime fingerprint is captured at package initialization.
    monkeypatch.setattr(
        "app.platform.domain.runtime_identity.capture_code_identity",
        lambda root: (_ for _ in ()).throw(AssertionError("disk reread")),
    )
    assert runtime_code_fingerprint() == loaded
    build, identity, artifact = fixture()
    with pytest.raises(PermanentJobError) as caught:
        validate_acceptance(
            artifact.model_copy(update={"code_fingerprint": new}),
            build,
            **{**identity, "code": old},
        )
    assert caught.value.code == "index_acceptance_stale"


async def test_evaluation_rejects_queued_runtime_mismatch_before_any_query():
    from types import SimpleNamespace

    from app.core.config import EvaluationConfig
    from app.modules.evaluation.services.evaluation_runner_service import EvaluationRunnerService

    runs = AsyncMock()
    run_id = uuid.uuid4()
    runs.get_by_id.return_value = SimpleNamespace(versions={"runtime_identity": "stale-runtime"})
    datasets, corpus, retrieval, answerer = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    runner = EvaluationRunnerService(
        runs=runs,
        datasets=datasets,
        corpus=corpus,
        retrieval=retrieval,
        answerer=answerer,
        config=EvaluationConfig(),
    )
    with pytest.raises(PermanentJobError) as caught:
        await runner.run(run_id)
    assert caught.value.code == "evaluation_runtime_mismatch"
    datasets.get_by_id.assert_not_awaited()
    corpus.snapshot.assert_not_awaited()
    retrieval.search.assert_not_awaited()


def test_captured_remediation_project_keeps_mandated_set_even_if_candidate_omits_document():
    from app.modules.retrieval.build_acceptance import (
        REMEDIATION_CASE_IDS,
        REMEDIATION_PROJECT_ID,
        validate_definition,
    )

    build, _, _ = fixture()
    build.project_id = REMEDIATION_PROJECT_ID
    generic = AcceptanceSetDefinition(
        project_id=build.project_id,
        build_id=build.id,
        revision="unrelated.v1",
        certification="production",
        repetitions=3,
        require_comparison=True,
        case_expectations={"other": "answered"},
    )
    with pytest.raises(PermanentJobError) as caught:
        validate_definition(generic, build)
    assert caught.value.code == "index_acceptance_set_incomplete"
    approved = generic.model_copy(
        update={
            "revision": "message-journey.v1",
            "case_expectations": dict.fromkeys(REMEDIATION_CASE_IDS, "unresolved_authority"),
        }
    )
    # Synthetic policy fixture only: no actual corpus result/label is certified here.
    validate_definition(approved, build)


@pytest.mark.parametrize(
    "env,database,provider",
    [
        ("testing", "ape_test", "fixture-concept"),
        ("production", "ape_test", "fixture-concept"),
        ("testing", "ape", "fixture-concept"),
        ("testing", "ape_test", "cohere"),
    ],
)
def test_concept_receipt_is_isolated_and_identity_bound(monkeypatch, env, database, provider):
    from types import SimpleNamespace

    build, identity, artifact = fixture()
    build.manifest = {**build.manifest, "embedding_provider": provider}
    artifact = artifact.model_copy(
        update={
            "embedding_identity": embedding_identity(build),
            "build_manifest_hash": digest(build.manifest),
        }
    )
    monkeypatch.setattr(
        "app.modules.retrieval.build_acceptance.get_settings",
        lambda: SimpleNamespace(
            app=SimpleNamespace(env=env),
            database=SimpleNamespace(name=database),
            test_database=SimpleNamespace(name="ape_test"),
            embedding=SimpleNamespace(backend=SimpleNamespace(value="hash")),
            llm=SimpleNamespace(backend=SimpleNamespace(value="echo")),
        ),
    )
    if env == "testing" and database == "ape_test" and provider == "fixture-concept":
        validate_acceptance(artifact, build, **identity)
        relabeled = artifact.model_copy(
            update={
                "embedding_identity": {**artifact.embedding_identity, "embedding_provider": "hash"}
            }
        )
        with pytest.raises(PermanentJobError, match="identities"):
            validate_acceptance(relabeled, build, **identity)
    else:
        with pytest.raises(PermanentJobError) as caught:
            validate_acceptance(artifact, build, **identity)
        assert caught.value.code == "index_acceptance_offline_only"
