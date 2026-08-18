"""Versioned retrieval-translation prompt. Output is a query, never an answer."""

from __future__ import annotations

PROMPT_VERSION = "retrieval-translation-v1"

SYSTEM_PROMPT = (
    "You translate a user search query into one target language for document "
    "retrieval.\n"
    "\n"
    "Rules:\n"
    "- Return only the translated retrieval query. No quotes, labels, or explanation.\n"
    "- Do not answer the question.\n"
    "- Keep the meaning of a formal legal/tax/financial document search.\n"
    "- Strictly preserve literals and identifiers exactly as written: section and "
    "article numbers, dates, percentages, amounts and currency figures, "
    "abbreviations, and quoted codes or terms.\n"
    "- Law names and entity names may be translated or given an established "
    "transliteration when that will match the target-language corpus better. Do not "
    "keep an English proper name only for the sake of string identity.\n"
    "- Do not add facts, commentary, or extra clauses.\n"
)


def translation_messages(
    *,
    query: str,
    target_language: str,
    source_profile: str,
) -> list[dict[str, str]]:
    user = (
        f"Source query profile: {source_profile}\n"
        f"Target language: {target_language}\n"
        f"Query:\n{query}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
