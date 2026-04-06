from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from anthropic import AsyncAnthropic

import config

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
PROMPT_PATH = Path(__file__).resolve().with_name("crops_project_scorer.md")


def _load_system_prompt() -> str:
    try:
        content = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing CROPS system prompt: {PROMPT_PATH}") from exc

    # Only strip YAML frontmatter if file starts with ---
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content


SYSTEM_PROMPT = _load_system_prompt()

USER_WRAPPER = """Score the following Ethereum project against the CROPS framework.

TODAY'S DATE IS {today}. When evaluating license conversion dates (e.g. "BUSL converts \
to GPL on 2026-01-01") or any other time-sensitive fact, apply this date. If a conversion \
date has already passed, the license has ALREADY converted — reflect that in the score.

Your response MUST begin with exactly "CROPS Assessment:" on the very first line. Do NOT \
write any preamble, research notes, "key findings", "let me compile", or any explanation \
before the score card. Do NOT write a descriptive overview or summary of the project. \
Produce the score card and nothing else.

Required output format (copy this structure exactly, replacing bracketed placeholders):

CROPS Assessment: [Project Name]

| Property | Score | Numerical | Reason |
|----------|-------|-----------|--------|
| Censorship Resistance (CR) | [Pass/Weak/Fail] | [X/10] | [specific evidence: function names, dates, amounts] |
| Open Source (O) | [Pass/Weak/Fail] | [X/10] | [specific evidence: license, repo] |
| Privacy (P) | [Pass/Weak/Fail] | [X/10] | [specific evidence] |
| Security (S) | [Pass/Weak/Fail] | [X/10] | [specific evidence: audits, governance] |

Adoption Level: [Dominant / Medium / Niche / Minimal]
CROPS-Native: [Yes (Fully covered) / Yes (Needs adoption) / Weak options only / No] — [one-line explanation]
Aggregate Score: [X.X/10]
Aggregate Score (excl. Privacy): [X.X/10]

Use web search to verify current license, contract functions, audits, and governance. \
Cite specific evidence (function names like blacklist(), license identifiers like BSL-1.1, \
audit firms, dates). Apply anti-bleed rules strictly: censorship/freeze/blacklist evidence \
belongs in CR, license evidence in O, privacy/data exposure in P, audits/governance in S.
{github_context}
Project to score: {query}"""


def _build_user_message(query: str, github_context: str | None) -> str:
    context_block = f"\n{github_context}\n" if github_context else "\n"
    return USER_WRAPPER.format(
        query=query,
        today=date.today().isoformat(),
        github_context=context_block,
    )

MAX_CONTINUATIONS = 5


async def score_project(
    query: str,
    github_context: str | None = None,
) -> tuple[str, int, int]:
    """Score an Ethereum project against CROPS. Returns (text, input_tokens, output_tokens)."""
    user_message = _build_user_message(query, github_context)
    messages = [{"role": "user", "content": user_message}]

    all_text: list[str] = []
    total_input = 0
    total_output = 0

    for _ in range(MAX_CONTINUATIONS):
        response = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=8000,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 10,
            }],
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        for block in response.content:
            if block.type == "text":
                all_text.append(block.text)
                # Render Anthropic web_search citations as inline markdown links
                # so the output matches OpenAI's [hostname](url) style.
                citations = getattr(block, "citations", None) or []
                for cit in citations:
                    url = getattr(cit, "url", None)
                    if not url:
                        continue
                    hostname = urlparse(url).hostname or url
                    if hostname.startswith("www."):
                        hostname = hostname[4:]
                    all_text.append(f" ([{hostname}]({url}))")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":
            # Server-side tool loop hit pause; continue the turn.
            messages.append({"role": "assistant", "content": response.content})
            continue

        break  # any other stop reason — we're done

    # Concatenate text fragments with no separator — Anthropic's web_search
    # tool splits text on citation boundaries, and fragments are meant to flow
    # seamlessly into a single continuous document.
    return "".join(all_text), total_input, total_output
