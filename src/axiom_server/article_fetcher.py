"""Article Fetcher - Retrieves full text from news URLs."""

from __future__ import annotations

import logging
import re
from typing import Final

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Common boilerplate classes/ids to strip
BOILERPLATE_SELECTORS: Final[list[str]] = [
    "nav",
    "footer",
    "aside",
    "script",
    "style",
    ".sidebar",
    ".ads",
    ".social-share",
    ".comments",
    "#footer",
    "#header",
    ".menu",
    ".newsletter-signup",
    ".related-articles",
    ".author-bio",
]


def fetch_article_text(url: str, timeout: int = 15) -> str | None:
    """Fetch the main content of an article from a URL.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        The cleaned text content of the article, or None if fetching fails.

    """
    try:
        # Mimic a real browser to avoid 404/403 stealth blocks from WAFs (Cloudflare/Akamai)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        # Jitter: wait a random short time to avoid being flagged as a bursty bot
        import random
        import time

        time.sleep(random.uniform(1.0, 3.0))  # noqa: S311

        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Remove common boilerplate
        for selector in BOILERPLATE_SELECTORS:
            for element in soup.select(selector):
                element.decompose()

        # Heuristic: Find the main article body
        # Most modern news sites use <article> or specific IDs/classes for the main body
        article_body = (
            soup.find("article")
            or soup.find(id="main-content")
            or soup.find(class_="article-body")
        )

        if article_body:
            text = article_body.get_text(separator="\n", strip=True)
        else:
            # Fallback: Just take all paragraphs from the remaining body
            paragraphs = soup.find_all("p")
            text = "\n".join(
                [
                    p.get_text(strip=True)
                    for p in paragraphs
                    if len(p.get_text()) > 50
                ],
            )

        # Clean up whitespace
        text = re.sub(r"\n+", "\n", text).strip()

        if len(text) < 200:
            logger.warning(
                f"Fetched content from {url} is suspiciously short ({len(text)} chars).",
            )
            return None

        return text

    except Exception as exc:
        logger.warning(f"Failed to fetch article from {url}: {exc}")
        return None


def extract_metadata(url: str) -> dict[str, str]:
    """Provide a placeholder for metadata extraction (date, author, etc.)."""
    # This will be expanded in later steps of Phase 2
    return {"source_url": url}
