"""Detect GitHub URLs in user input and fetch repo metadata + README."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import httpx

GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)

API_BASE = "https://api.github.com"
README_TRUNCATE = 6000  # chars


@dataclass
class GitHubContext:
    owner: str
    repo: str
    full_name: str
    description: str | None
    license_spdx: str | None
    license_name: str | None
    stars: int
    forks: int
    archived: bool
    default_branch: str
    language: str | None
    pushed_at: str | None
    readme_excerpt: str | None

    def to_prompt_block(self) -> str:
        """Render as a structured context block for the AI."""
        lines = [
            f"=== GITHUB REPO CONTEXT (authoritative facts from GitHub API) ===",
            f"Repository: {self.full_name}",
        ]
        if self.description:
            lines.append(f"Description: {self.description}")
        if self.license_spdx:
            lines.append(f"License (SPDX): {self.license_spdx}")
            if self.license_name:
                lines.append(f"License name: {self.license_name}")
        else:
            lines.append("License: NO LICENSE FILE in repository (= all rights reserved under copyright law)")
        lines.append(f"Stars: {self.stars} | Forks: {self.forks}")
        if self.archived:
            lines.append("Status: ARCHIVED (no longer maintained)")
        if self.language:
            lines.append(f"Primary language: {self.language}")
        if self.pushed_at:
            lines.append(f"Last push: {self.pushed_at}")
        if self.readme_excerpt:
            lines.append("")
            lines.append("--- README excerpt ---")
            lines.append(self.readme_excerpt)
            lines.append("--- end README ---")
        lines.append("=== END GITHUB CONTEXT ===")
        return "\n".join(lines)


def find_github_url(text: str) -> tuple[str, str] | None:
    """Return (owner, repo) if a github.com URL is found in text, else None."""
    match = GITHUB_URL_RE.search(text)
    if not match:
        return None
    owner = match.group(1)
    repo = match.group(2)
    # Strip common trailing junk like .git
    repo = re.sub(r"\.git$", "", repo)
    return owner, repo


async def fetch_github_context(owner: str, repo: str) -> GitHubContext | None:
    """Fetch repo metadata and README from GitHub. Returns None if repo not found."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "crops-inspector-bot",
    }

    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        # Repo metadata
        repo_resp = await client.get(f"{API_BASE}/repos/{owner}/{repo}")
        if repo_resp.status_code == 404:
            return None
        repo_resp.raise_for_status()
        meta = repo_resp.json()

        license_obj = meta.get("license") or {}
        license_spdx = license_obj.get("spdx_id") if license_obj else None
        license_name = license_obj.get("name") if license_obj else None
        # GitHub returns "NOASSERTION" for unrecognized licenses; treat as missing
        if license_spdx in ("NOASSERTION", None, ""):
            license_spdx = None

        # README (separate endpoint, returns base64 content)
        readme_excerpt: str | None = None
        try:
            readme_resp = await client.get(f"{API_BASE}/repos/{owner}/{repo}/readme")
            if readme_resp.status_code == 200:
                content_b64 = readme_resp.json().get("content", "")
                if content_b64:
                    decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                    readme_excerpt = decoded[:README_TRUNCATE]
                    if len(decoded) > README_TRUNCATE:
                        readme_excerpt += "\n... [README truncated] ..."
        except Exception:
            pass  # README is optional

    return GitHubContext(
        owner=owner,
        repo=repo,
        full_name=meta.get("full_name", f"{owner}/{repo}"),
        description=meta.get("description"),
        license_spdx=license_spdx,
        license_name=license_name,
        stars=meta.get("stargazers_count", 0),
        forks=meta.get("forks_count", 0),
        archived=bool(meta.get("archived", False)),
        default_branch=meta.get("default_branch", "main"),
        language=meta.get("language"),
        pushed_at=meta.get("pushed_at"),
        readme_excerpt=readme_excerpt,
    )
