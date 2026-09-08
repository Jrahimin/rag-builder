"""Replay fixed tax questions using production composition and a local existing corpus.

No uploads, source changes, or cleanup. Each case creates a labelled test conversation.
Use --backend to compare a saved checkout with the same database/config/providers.
"""

from __future__ import annotations

# CLI selects the checkout before importing its production composition.
# ruff: noqa: E402, T201
import argparse
import asyncio
import hashlib
import itertools
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--backend", type=Path, default=ROOT / "backend")
parser.add_argument("--project", default="2ee2756f-ad27-44df-a9d3-1316b10ccbb1")
parser.add_argument("--label", default="optimized")
parser.add_argument("--repeat", type=int, default=3)
parser.add_argument(
    "--domain-instructions-file",
    type=Path,
    help="Replay a reviewed policy through an immutable revision; restore the original afterward",
)
parser.add_argument(
    "--context-ceilings",
    nargs="+",
    type=int,
    help="Compare context ceilings through immutable revisions; restore original policy afterward",
)
parser.add_argument(
    "--as-of", type=datetime.fromisoformat, help="Explicit UTC source cutoff for historical replay"
)
parser.add_argument("--cases", nargs="+", default=["gross", "taxable", "rebate", "historical"])
args = parser.parse_args()
if args.repeat < 1 or (args.as_of is not None and args.as_of.tzinfo is None):
    parser.error("repeat must be positive and as-of must include a timezone")
code_digest = hashlib.sha256()
for code_path in sorted((args.backend / "app").rglob("*.py")):
    code_digest.update(str(code_path.relative_to(args.backend)).encode())
    code_digest.update(code_path.read_bytes())
CODE_HASH = code_digest.hexdigest()
sys.path.insert(0, str(args.backend))

from app.core import config as config_module

# Pin the same deployment settings even when code is loaded from an archived checkout.
_settings = config_module.Settings(_env_file=ROOT / "backend" / ".env")
config_module.get_settings = lambda: _settings
from app.composition.audit import DatabaseAuditRecorder
from app.core.config import get_settings
from app.dependencies.conversations import get_chat_service
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.conversation import ConversationCreate
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.conversation_service import ConversationService
from app.modules.projects.repositories.project_ai_config_repository import ProjectAIConfigRepository
from app.modules.projects.services.project_ai_config_service import ProjectAdministrationService
from app.platform.config.project_ai import ProjectAIConfig, config_revision_record
from app.platform.db.session import Database
from app.platform.providers.implementations.embedding_factory import get_embedding_provider

QUESTIONS = {
    "gross_with_interest": (
        "Suppose My yearly salary 1200000 BDT. Rebateable investment 60000 BDT. "
        "I am a male from chittagong. Age below 40.\n"
        "I got 24000 as interest from Sanchaypatra in the financial year. from bank interest, "
        "got 2200. consider all latest financial year.\n"
        "based on these info can you calculate tax and also provide a breakdown which rules "
        "applied etc. to know how it is calculated."
    ),
    "film_direct": "Who directed the fictional film Lantern Harbor?",
    "film_conflict": (
        "Compare the premiere year for Lantern Harbor in the Film Catalogue and Festival Record. "
        "Attribute each date."
    ),
    "competing_accounts": (
        "Compare the Vale Account and Marsh Account explanations of the harbor closure. "
        "Does the Vale reprint count as another work?"
    ),
    "gross": (
        "Suppose My yearly salary 1200000 BDT. Rebateable investment 60000 BDT. "
        "I am a male from chittagong. Age below 40. based on these info can you calculate tax "
        "and also provide a breakdown which rules applied etc. to know how it is calculated."
    ),
    "taxable": (
        "For AY 2026-27, calculate income tax for a resident male below 40 in Chattogram City "
        "Corporation, with BDT 1,200,000 already taxable after exemptions and BDT 60,000 "
        "qualifying investment. Do not deduct a salary exemption again. Show the applicable "
        "rebate and minimum-tax alternatives."
    ),
    "rebate": "What is the current investment tax rebate rate and limit?",
    "historical": (
        "For assessment year 2024-25, what investment tax rebate formula applied? "
        "Use that historical period, not the current amendment."
    ),
}


async def main() -> None:
    settings = get_settings()
    if settings.database.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This evaluator requires a local database")
    db = Database(settings)
    project = uuid.UUID(args.project)
    output = ROOT / "artifacts" / "rag-enhancements" / f"{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    original_revision = None
    owned_revision_id = None
    active_ceiling = None
    policy_text = (
        args.domain_instructions_file.read_text(encoding="utf-8")
        if args.domain_instructions_file
        else None
    )
    if args.context_ceilings or policy_text is not None:
        if any(not 1 <= value <= 50 for value in args.context_ceilings or []):
            raise ValueError("Context ceilings must be between 1 and 50")
        async with db.session_factory() as session:
            original_revision = await ProjectAIConfigRepository(session, project).get_active()
            if original_revision is None or original_revision.schema_version != 2:
                raise ValueError("Context sweep requires an existing V2 Project revision")
            owned_revision_id = original_revision.id

    async def change_policy(ceiling: int | None, *, restore: bool = False) -> None:
        nonlocal owned_revision_id
        assert original_revision is not None
        configuration = json.loads(json.dumps(original_revision.configuration))
        if ceiling is not None:
            configuration["execution"].update(profile_id="custom", max_context_chunks=ceiling)
        if policy_text is not None and not restore:
            configuration["behavior"]["domain_instructions"] = policy_text
        async with db.session_factory() as session:
            administration = ProjectAdministrationService(
                session=session,
                project_id=project,
                repository=ProjectAIConfigRepository(session, project),
                settings=settings,
                audit=DatabaseAuditRecorder(session, project),
                actor_id="rag-enhancement-evaluation",
            )
            revision = await administration.create_revision(
                ProjectAIConfig.model_validate(configuration),
                expected_active_revision_id=owned_revision_id,
                restored_from_revision_id=original_revision.id if restore else None,
                reason=f"RAG evaluation: ceiling {ceiling}, policy replay {policy_text is not None}"
                if not restore
                else "Restore original policy after context evaluation",
            )
            owned_revision_id = revision.id

    try:
        if policy_text is not None:
            await change_policy(None)
        for ceiling, repetition, case in itertools.product(
            args.context_ceilings or [None], range(args.repeat), args.cases
        ):
            if ceiling != active_ceiling:
                await change_policy(ceiling)
                active_ceiling = ceiling
            async with db.session_factory() as session:
                conversations = ConversationRepository(session, project)
                messages = MessageRepository(session, project)
                active = await ProjectAIConfigRepository(session, project).get_active()
                service = ConversationService(
                    session,
                    project,
                    conversations,
                    messages,
                    llm_config=settings.llm,
                    chat_config=settings.chat,
                    settings=settings,
                    active_revision=config_revision_record(active),
                    actor_id="rag-enhancement-evaluation",
                    audit=DatabaseAuditRecorder(session, project),
                )
                conversation = await service.create(
                    ConversationCreate(
                        title=f"RAG enhancement {args.label}: {case} #{repetition + 1}"
                    )
                )
                started = time.perf_counter()
                try:
                    chat = await get_chat_service(
                        session,
                        project,
                        conversations,
                        messages,
                        conversation.id,
                        get_embedding_provider(),
                    )
                    turn = await chat.send_message(
                        conversation.id,
                        MessageSendRequest(content=QUESTIONS[case], as_of=args.as_of),
                    )
                    record = {
                        "case": case,
                        "code_hash": CODE_HASH,
                        "context_ceiling": ceiling,
                        "as_of": args.as_of.isoformat() if args.as_of else None,
                        "repeat": repetition + 1,
                        "conversation_id": str(conversation.id),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "assistant": turn.assistant_message.model_dump(mode="json"),
                    }
                except Exception as exc:
                    record = {
                        "case": case,
                        "repeat": repetition + 1,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                records.append(record)
                output.write_text(
                    json.dumps(records, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                assistant = record.get("assistant", {})
                print(
                    case,
                    repetition + 1,
                    record.get("elapsed_ms"),
                    assistant.get("insufficient_evidence_reason")
                    or record.get("error_type")
                    or "answered",
                    flush=True,
                )
    finally:
        try:
            if original_revision is not None and owned_revision_id != original_revision.id:
                await change_policy(None, restore=True)
        finally:
            await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
