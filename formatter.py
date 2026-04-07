from __future__ import annotations

import re
from html import escape


def format_for_telegram(raw_markdown: str) -> list[str]:
    """Convert CROPS scorer output into a list of Telegram HTML messages."""
    if not raw_markdown or not raw_markdown.strip():
        return ["No results generated."]

    # Strip code fences if model wrapped output in ```
    raw_markdown = _strip_code_fences(raw_markdown)
    # Strip any preamble before "CROPS Assessment:"
    raw_markdown = _strip_preamble(raw_markdown)

    # Parse the score card
    title = _extract_title(raw_markdown)
    rows = _parse_score_table(raw_markdown)
    raw_metadata = _extract_metadata(raw_markdown)

    if not _has_complete_score_table(rows):
        return _split_messages(escape(raw_markdown))

    # Compute correct aggregates and CROPS-Native from the parsed scores
    # (overriding whatever the model wrote — it makes math and logic errors)
    adoption = _extract_adoption(raw_metadata)
    # Override numerical scores with flat Pass/Weak/Fail mapping (10/5/0)
    _apply_score_matrix(rows)
    computed_metadata = _build_computed_metadata(rows, adoption)

    parts: list[str] = []

    # Title
    if title:
        parts.append(f"<b>{escape(title)}</b>")

    # Score summary line
    scores = {r["property_short"]: r["score"] for r in rows if r.get("property_short")}
    if scores:
        summary = " | ".join(f"{k}: {_format_score(v)}" for k, v in scores.items())
        parts.append(summary)

    # Numerical scores (using corrected values from the matrix)
    numericals = {r["property_short"]: r["numerical"] for r in rows if r.get("numerical")}
    if numericals:
        num_line = " | ".join(f"{k}: {escape(v)}" for k, v in numericals.items())
        parts.append(num_line)

    parts.append("")  # blank line

    # Per-property details
    for row in rows:
        prop = row.get("property", "")
        score = row.get("score", "")
        numerical = row.get("numerical", "")
        reason = row.get("reason", "")

        header = f"<b>{escape(prop)}</b>: {_format_score(score)}"
        if numerical:
            header += f" ({escape(numerical)})"
        parts.append(header)

        if reason:
            # Truncate very long reasons (800 to leave room for link tags)
            reason_text = reason[:800] + "..." if len(reason) > 800 else reason
            parts.append(_render_reason_html(reason_text))
        parts.append("")  # blank line between properties

    # Metadata (adoption from model, CROPS-native + aggregates computed in Python)
    for line in computed_metadata:
        if ":" in line:
            label, value = line.split(":", 1)
            parts.append(f"<b>{escape(label.strip())}:</b> {escape(value.strip())}")
        else:
            parts.append(escape(line))

    # Scoring explanation footer — helps users understand why a Pass on a small
    # project doesn't score 10/10
    parts.append("")
    parts.append(SCORING_EXPLANATION)

    result = "\n".join(parts).strip()
    return _split_messages(result)


SCORING_EXPLANATION = (
    "<i>How scoring works: each property is scored <b>Pass = 10</b>, "
    "<b>Weak = 5</b>, or <b>Fail = 0</b>. Aggregate is the average across all "
    "four properties. Adoption is shown as a separate label and affects the "
    "CROPS-Native verdict (Fully Covered vs Needs Adoption), but not the "
    "numerical scores — those reflect the protocol's properties, not its "
    "market share.</i>"
)


def _extract_title(md: str) -> str:
    """Extract 'CROPS Assessment: Project Name' from the output."""
    for line in md.split("\n"):
        line = line.strip()
        if line.lower().startswith("crops assessment"):
            return line
        # Also match markdown headers with project name
        if line.startswith("#") and "crops" in line.lower():
            return line.lstrip("#").strip()
    return ""


def _parse_score_table(md: str) -> list[dict]:
    """Parse the Property | Score | Numerical | Reason table."""
    rows: list[dict] = []
    lines = md.split("\n")
    in_table = False

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break  # end of table
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]

        # Skip separator rows
        if cells and all(re.match(r"^[-:]+$", c) for c in cells):
            in_table = True
            continue

        # Skip header row (contains "Property" or "Score")
        if cells and any("property" in c.lower() for c in cells):
            in_table = True
            continue

        if not in_table or len(cells) < 3:
            continue

        prop = cells[0].strip()
        score = cells[1].strip() if len(cells) > 1 else ""
        numerical = cells[2].strip() if len(cells) > 2 else ""
        reason = " | ".join(cells[3:]).strip() if len(cells) > 3 else ""

        # Extract short property name (CR, O, P, S)
        short = _property_short(prop)

        rows.append({
            "property": prop,
            "property_short": short,
            "score": score,
            "numerical": numerical,
            "reason": reason,
        })

    return rows


def _has_complete_score_table(rows: list[dict]) -> bool:
    required_properties = {"CR", "O", "P", "S"}
    found_properties = {row["property_short"] for row in rows if row.get("property_short")}
    return required_properties.issubset(found_properties)


def _property_short(prop: str) -> str:
    """Extract short name like CR, O, P, S from property name."""
    p = prop.lower()
    if "censorship" in p:
        return "CR"
    if "open" in p:
        return "O"
    if "privacy" in p:
        return "P"
    if "security" in p:
        return "S"
    # Try parenthetical: "Censorship Resistance (CR)"
    m = re.search(r"\((\w+)\)", prop)
    if m:
        return m.group(1)
    return prop[:2]


def _extract_metadata(md: str) -> list[str]:
    """Extract non-table metadata lines (Adoption, CROPS-Native, Aggregate)."""
    metadata: list[str] = []
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith("#"):
            continue
        lower = stripped.lower()
        if any(k in lower for k in ["adoption level", "crops-native", "aggregate score"]):
            metadata.append(stripped)
    return metadata


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _render_reason_html(text: str) -> str:
    """Escape text for HTML and convert [label](url) markdown links to <a> tags."""
    # Find all markdown links first and substitute them with placeholders
    # so the URLs don't get corrupted by escaping.
    placeholders: list[str] = []

    def stash(m: "re.Match[str]") -> str:
        label = escape(m.group(1))
        url = m.group(2).replace('"', "%22")
        placeholders.append(f'<a href="{url}">{label}</a>')
        return f"\x00LINK{len(placeholders) - 1}\x00"

    stashed = _MD_LINK_RE.sub(stash, text)
    escaped = escape(stashed)
    # Restore the link placeholders
    for i, html in enumerate(placeholders):
        escaped = escaped.replace(f"\x00LINK{i}\x00", html)
    return escaped


def _format_score(score: str) -> str:
    """Format a Pass/Weak/Fail score with bold."""
    s = score.strip().lower()
    if s == "pass":
        return "<b>Pass</b>"
    elif s == "fail":
        return "<b>Fail</b>"
    elif s == "weak":
        return "<b>Weak</b>"
    return escape(score)


def _strip_code_fences(md: str) -> str:
    """Remove wrapping ``` code fences if present."""
    lines = md.strip().split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _strip_preamble(md: str) -> str:
    """Remove any text before 'CROPS Assessment:'."""
    lower = md.lower()
    idx = lower.find("crops assessment:")
    if idx > 0:
        return md[idx:]
    return md


def _parse_numerical(numerical: str) -> float | None:
    """Parse 'X/10' or 'X.X' into a float."""
    m = re.match(r"(\d+(?:\.\d+)?)", numerical.strip())
    if m:
        return float(m.group(1))
    return None


# Flat categorical-only scoring. Adoption is shown separately as a label and
# influences only the CROPS-Native verdict (Fully Covered vs Needs Adoption),
# not the per-property numerical scores. This keeps numbers honest about
# property quality, which is what users asking "is X CROPS?" actually want.
SCORE_MATRIX: dict[str, float] = {
    "pass": 10,
    "weak": 5,
    "fail": 0,
}


def _matrix_score(categorical: str) -> float | None:
    """Look up the canonical numerical score from the categorical."""
    return SCORE_MATRIX.get(categorical.strip().lower())


def _format_num(n: float) -> str:
    """Render a numerical score, dropping trailing .0."""
    if n == int(n):
        return f"{int(n)}/10"
    return f"{n}/10"


def _apply_score_matrix(rows: list[dict]) -> None:
    """Override each row's 'numerical' field with the canonical Pass/Weak/Fail value.

    Mutates rows in place.
    """
    for row in rows:
        n = _matrix_score(row.get("score", ""))
        if n is not None:
            row["numerical"] = _format_num(n)


def _extract_adoption(metadata: list[str]) -> str:
    """Extract the adoption level value from metadata lines."""
    for line in metadata:
        if line.lower().startswith("adoption level"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return ""


def _build_computed_metadata(rows: list[dict], adoption: str) -> list[str]:
    """Build the metadata section with computed aggregates and CROPS-Native status."""
    # Parse numerical scores by property
    scores: dict[str, float] = {}
    categoricals: dict[str, str] = {}
    for row in rows:
        prop = row.get("property_short", "")
        num = _parse_numerical(row.get("numerical", ""))
        cat = row.get("score", "").strip().lower()
        if prop and num is not None:
            scores[prop] = num
        if prop and cat:
            categoricals[prop] = cat

    lines: list[str] = []

    if adoption:
        lines.append(f"Adoption Level: {adoption}")

    # Derive CROPS-Native status from categorical scores
    required = ["CR", "O", "P", "S"]
    if all(p in categoricals for p in required):
        statuses = [categoricals[p] for p in required]
        fails = [p for p in required if categoricals[p] == "fail"]
        weaks = [p for p in required if categoricals[p] == "weak"]

        if fails:
            crops_native = f"No — fails {', '.join(fails)}"
        elif weaks:
            crops_native = f"Weak options only — weak on {', '.join(weaks)}"
        elif all(s == "pass" for s in statuses):
            if adoption.lower() in ("dominant", "medium"):
                crops_native = "Yes (Fully covered) — all four properties pass"
            else:
                crops_native = "Yes (Needs adoption) — all four pass but adoption is limited"
        else:
            crops_native = "Unknown"
        lines.append(f"CROPS-Native: {crops_native}")

    # Compute aggregate from numerical scores
    if all(p in scores for p in required):
        aggregate = sum(scores[p] for p in required) / 4
        lines.append(f"Aggregate Score: {aggregate:.1f}/10")

    return lines


def _split_messages(text: str) -> list[str]:
    """Split text into messages under 3800 chars at paragraph boundaries."""
    if len(text) <= 3800:
        return [text]

    messages: list[str] = []
    while text:
        if len(text) <= 3800:
            messages.append(text)
            break
        idx = text.rfind("\n\n", 0, 3800)
        if idx == -1:
            idx = text.rfind("\n", 0, 3800)
        if idx == -1:
            idx = 3800
        messages.append(text[:idx].strip())
        text = text[idx:].lstrip("\n")

    return messages


