"""Discovery RSS - Find news from RSS."""

from __future__ import annotations

# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
import logging
import random
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)  # <-- NEW IMPORT
from typing import Final

import feedparser
import requests

logger = logging.getLogger(__name__)

# High-signal, authoritative news sources and fact-checkers.
HIGH_PRIORITY_FEEDS: Final[tuple[str, ...]] = (
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://www.reutersagency.com/feed/?best-topics=world-news&post_type=best",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://apnews.my.id/feed",
    "https://www.politifact.com/rss/all/",
    "https://www.bellingcat.com/feed/",
    "https://www.propublica.org/feeds/propublica/main",
    "https://api.axios.com/feed/",
    "https://www.ft.com/rss/home/international",
)

# Secondary sources for broader coverage and diversity.
SECONDARY_FEEDS: Final[tuple[str, ...]] = (
    "https://www.theguardian.com/world/rss",
    "https://www.npr.org/rss/rss.php?id=1001",
    "https://rss.dw.com/rdf/rss-en-all",
    "https://foreignpolicy.com/feed/",
    "https://www.abc.net.au/news/feed/51120/rss.xml",
    "https://www.spiegel.de/international/index.rss",
    "https://www.lemonde.fr/en/rss/full_feed.xml",
    "https://www.japantimes.co.jp/feed",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "https://www.latimes.com/index.rss",
    "https://chicago.suntimes.com/feed/",
    "https://thehill.com/rss/syndicator/19110",
    "https://www.cbsnews.com/latest/rss/main",
    "https://feeds.nbcnews.com/nbcnews/public/news",
    "https://www.wired.com/feed/rss",
    "https://www.technologyreview.com/feed/",
    "https://spectrum.ieee.org/rss/fulltext",
    "https://www.scientificamerican.com/platform/syndication/rss/",
    "https://www.nature.com/nature.rss",
    "https://science.sciencemag.org/rss/current.xml",
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://arstechnica.com/feed/",
    "https://techcrunch.com/feed/",
    "https://www.ft.com/rss/home",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://www.themarshallproject.org/rss/recent",
    "https://www.icij.org/feed/",
    "https://www.statnews.com/feed/",
    "https://www.sciencedaily.com/rss/all.xml",
    "https://insideclimatenews.org/feed/",
    "https://rss.csmonitor.com/feeds/all",
    "https://www.pbs.org/newshour/feeds/rss/headlines",
    "https://time.com/feed",
    "https://vtdigger.org/feed/",
    "https://www.seattletimes.com/feed/",
)

# Combined list for backwards compatibility if needed
RSS_FEEDS: Final[tuple[str, ...]] = HIGH_PRIORITY_FEEDS + SECONDARY_FEEDS


def get_content_from_prioritized_feed(
    max_items: int = 5,
) -> list[dict[str, str]]:
    """Select and processes a single, valid RSS feed to find new content.

    This function is resilient: it shuffles the feeds and tries them one by
    one until it finds a valid one to process, preventing a single broken
    feed from stopping a fact-finding cycle.
    """
    shuffled_feeds = list(RSS_FEEDS)
    random.shuffle(shuffled_feeds)

    if not shuffled_feeds:
        logger.warning("No RSS feeds configured.")
        return []

    for feed_url in shuffled_feeds:
        logger.info(f"Attempting to process feed: {feed_url}")
        try:
            response = requests.get(
                feed_url,
                timeout=10,
                headers={"User-Agent": "AxiomEngine/1.0"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo:
                logger.warning(
                    f"Feed is malformed, skipping: {feed_url}. Reason: {feed.bozo_exception}",
                )
                continue

            content_list = []
            for entry in feed.entries[:max_items]:
                source_url = entry.get("link")
                content = entry.get("summary", entry.get("description", ""))
                published_date = entry.get(
                    "published",
                    entry.get("updated", ""),
                )
                if source_url and content:
                    content_list.append(
                        {
                            "source_url": source_url,
                            "content": content,
                            "published_date": published_date,
                        },
                    )

            if content_list:
                logger.info(
                    f"Successfully extracted {len(content_list)} items from {feed_url}.",
                )
                return content_list
            logger.info(
                f"Feed {feed_url} was valid but contained no new items. Trying next.",
            )

        except Exception as exc:
            logger.warning(
                f"An unexpected error occurred for feed {feed_url}. Skipping. Error: {exc}",
            )
            continue

    logger.error(
        "Failed to retrieve content from ANY of the configured RSS feeds.",
    )
    return []


# --- NEW HELPER FUNCTION for concurrent fetching ---
def _fetch_one_feed_headlines(feed_url: str) -> list[str]:
    """Worker function to fetch and parse a single RSS feed.

    Designed to be called concurrently. Returns a list of headlines.
    """
    try:
        response = requests.get(
            feed_url,
            timeout=10,
            headers={"User-Agent": "AxiomEngine/1.0"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        if feed.bozo:
            # Silently ignore malformed feeds in concurrent mode to avoid log spam.
            return []

        headlines = []
        for entry in feed.entries:
            headline = entry.get("title", "")
            if headline:
                headlines.append(headline)
        return headlines

    except Exception:
        # If any other error occurs (e.g., network timeout), silently fail for this feed.
        return []


# --- main function to be concurrent ---
def get_all_headlines_from_feeds() -> list[str]:
    """Fetch headlines concurrently from a prioritized selection of RSS feeds.

    Ensures that high-priority, high-signal feeds are always preferred while
    maintaining diversity through random sampling of secondary sources.
    """
    high_priority = list(HIGH_PRIORITY_FEEDS)
    secondary = list(SECONDARY_FEEDS)

    random.shuffle(high_priority)
    random.shuffle(secondary)

    # Take up to 3 from high priority and 2 from secondary to reach 5 total
    selected_feeds = high_priority[:3] + secondary[:2]
    random.shuffle(selected_feeds)  # Shuffle the final selection

    all_headlines: list[str] = []

    logger.info(
        f"Fetching headlines from {len(selected_feeds)} prioritized sources (High: {len(high_priority[:3])}, Secondary: {len(secondary[:2])})...",
    )

    # Use a ThreadPoolExecutor to run up to 5 requests (one per selected feed) at the same time.
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Create a dictionary mapping future tasks to their URLs
        future_to_url = {
            executor.submit(_fetch_one_feed_headlines, url): url
            for url in selected_feeds
        }

        # Process the results as they complete
        for future in as_completed(future_to_url):
            try:
                headlines_from_one_feed = future.result()
                all_headlines.extend(headlines_from_one_feed)
            except Exception as exc:
                url = future_to_url[future]
                logger.warning(
                    f"Concurrent fetch for {url} generated an exception: {exc}",
                )

    # Final random shuffle of headlines to further ensure nodes don't process in the same order
    random.shuffle(all_headlines)

    logger.info(
        f"Fetched and shuffled a total of {len(all_headlines)} headlines.",
    )
    return all_headlines
