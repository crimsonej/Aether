"""Approved public-source web access for Aether data gathering.

This layer is intentionally narrow: it only permits HTTPS fetches against a
known allowlist and strips the result down to structured text for downstream use.
No arbitrary browser automation, login flows, or unrestricted scraping are
exposed to the agent.
"""

from __future__ import annotations

import html
import io
import ipaddress
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import aiohttp


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return " ".join(part.strip() for part in self.parts if part.strip())


class ApprovedWebDataTool:
    DEFAULT_ALLOWLIST = {
        "finance.yahoo.com",
        "query1.finance.yahoo.com",
        "www.alphavantage.co",
        "www.coingecko.com",
        "api.coingecko.com",
        "www.frankfurter.app",
        "api.frankfurter.app",
        "www.reuters.com",
        "www.cnbc.com",
        "www.marketwatch.com",
        "www.bloomberg.com",
        "www.investing.com",
        "www.fxstreet.com",
        "www.forexfactory.com",
        "www.dailyfx.com",
        "duckduckgo.com",
        "www.yahoo.com",
    }
    MAX_BYTES = 2_000_000

    def __init__(self, allowlist: Optional[set[str]] = None, session_factory=None):
        self.allowlist = set(allowlist or self.DEFAULT_ALLOWLIST)
        self.session_factory = session_factory or aiohttp.ClientSession

    @classmethod
    def is_allowed_url(cls, url: str, allowlist: Optional[set[str]] = None) -> bool:
        try:
            parsed = urlparse(str(url))
        except Exception:
            return False
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower().strip("[]")
        if not hostname:
            return False
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return False
        try:
            ipaddress.ip_address(hostname)
            return False
        except ValueError:
            pass
        allowed = allowlist or cls.DEFAULT_ALLOWLIST
        return hostname in allowed or any(hostname.endswith(f".{dom}") for dom in allowed)

    @staticmethod
    def strip_html(raw_html: str) -> str:
        parser = _HTMLTextExtractor()
        parser.feed(raw_html or "")
        parser.close()
        return parser.get_text()

    @staticmethod
    def extract_title(raw_html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return "Untitled"
        return " ".join(html.unescape(match.group(1)).split())

    @staticmethod
    def summarize_text(raw_html: str, max_chars: int = 1200) -> str:
        text = ApprovedWebDataTool.strip_html(raw_html)
        summary = " ".join(text.split())
        return summary[:max_chars]

    @classmethod
    def extract_search_results(cls, raw_html: str) -> List[Dict[str, str]]:
        matches = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        results: List[Dict[str, str]] = []
        seen: set[str] = set()
        for href, inner_html in matches:
            text = re.sub(r"<[^>]+>", " ", inner_html, flags=re.DOTALL)
            text = html.unescape(" ".join(text.split()))
            if not text:
                continue
            if href.startswith("/") or href.startswith("javascript:"):
                continue
            if not cls.is_allowed_url(href):
                continue
            url = href
            if url in seen:
                continue
            seen.add(url)
            results.append({"title": text, "url": url})
            if len(results) >= 10:
                break
        return results

    async def fetch_page(self, url: str) -> Dict[str, Any]:
        if not self.is_allowed_url(url, self.allowlist):
            raise ValueError("URL is not in the approved public source allowlist")
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with self.session_factory(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status >= 400:
                    raise RuntimeError(f"fetch failed with HTTP {response.status}")
                body = await response.read()
                if len(body) > self.MAX_BYTES:
                    raise ValueError("page exceeds the configured size limit")
                charset = response.charset or "utf-8"
                content = body.decode(charset, errors="replace")
        return {
            "url": url,
            "title": self.extract_title(content),
            "summary": self.summarize_text(content),
            "source": urlparse(url).netloc,
        }

    async def search_web(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        safe_query = quote_plus(query.strip())
        url = f"https://duckduckgo.com/html/?q={safe_query}"
        if not self.is_allowed_url(url, self.allowlist):
            raise ValueError("search engine URL is not in the approved public source allowlist")
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with self.session_factory(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status >= 400:
                    raise RuntimeError(f"search failed with HTTP {response.status}")
                body = await response.read()
                if len(body) > self.MAX_BYTES:
                    raise ValueError("search response exceeds the configured size limit")
                content = body.decode(response.charset or "utf-8", errors="replace")
        results = self.extract_search_results(content)
        return results[: max(1, int(limit))]
