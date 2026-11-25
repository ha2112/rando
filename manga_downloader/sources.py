"""Manga sources: each one can search, list English chapters, and list page image URLs."""

import json
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 30


@dataclass
class Series:
    source: str  # source name, e.g. "MangaDex"
    id: str  # whatever the source needs to fetch chapters (id or URL)
    title: str
    info: str = ""  # short extra text shown in search results


@dataclass
class Chapter:
    number: float  # used for range selection and sorting
    label: str  # human readable, e.g. "Ch. 10.5"
    id: str  # whatever the source needs to fetch pages (id or URL)
    referer: str = ""  # some image hosts want a Referer
    extra: dict = field(default_factory=dict)


def chapter_number(text, default=0.0):
    """Last number in a label: '# 134' -> 134, 'Vol 2 Chapter 10.5' -> 10.5."""
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return float(nums[-1]) if nums else default


def fmt_number(n):
    """10.0 -> '10', 10.5 -> '10.5'."""
    return str(int(n)) if n == int(n) else str(n)


class Source:
    name = ""
    user_agent = BROWSER_UA

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = self.user_agent

    def get(self, url, **kw):
        r = self.session.get(url, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        return r

    def search(self, query):  # -> list[Series]
        raise NotImplementedError

    def chapters(self, series):  # -> list[Chapter], ascending, one per number
        raise NotImplementedError

    def pages(self, chapter):  # -> list[str] image URLs in reading order
        raise NotImplementedError


class MangaDex(Source):
    name = "MangaDex"
    api = "https://api.mangadex.org"
    # MangaDex asks clients to identify themselves rather than pose as a browser
    user_agent = "manga-builder/1.0"
    # include every rating; MangaDex hides erotica/pornographic by default
    ratings = ["safe", "suggestive", "erotica", "pornographic"]

    @staticmethod
    def _title(attrs):
        # prefer an English title, then any title
        if "en" in attrs["title"]:
            return attrs["title"]["en"]
        for alt in attrs.get("altTitles", []):
            if "en" in alt:
                return alt["en"]
        return next(iter(attrs["title"].values()), "Untitled")

    def search(self, query):
        params = {
            "title": query,
            "limit": 10,
            "availableTranslatedLanguage[]": ["en"],
            "hasAvailableChapters": "true",
            "contentRating[]": self.ratings,
            "order[relevance]": "desc",
        }
        data = self.get(f"{self.api}/manga", params=params).json()["data"]
        results = []
        for m in data:
            a = m["attributes"]
            info = " · ".join(str(x) for x in (a.get("year"), a.get("status")) if x)
            results.append(Series(self.name, m["id"], self._title(a), info))
        return results

    def chapters(self, series):
        found = {}  # number -> Chapter (first upload wins on duplicates)
        offset, total = 0, 1
        while offset < total:
            params = {
                "translatedLanguage[]": ["en"],
                "contentRating[]": self.ratings,
                "includeExternalUrl": 0,  # skip chapters hosted off-site
                "order[chapter]": "asc",
                "limit": 500,
                "offset": offset,
            }
            body = self.get(f"{self.api}/manga/{series.id}/feed", params=params).json()
            total = body["total"]
            offset += len(body["data"]) or total  # stop if a page comes back empty
            for c in body["data"]:
                a = c["attributes"]
                if not a.get("pages"):
                    continue
                num = float(a["chapter"]) if a.get("chapter") else 0.0
                if num in found:
                    continue
                label = f"Ch. {a['chapter']}" if a.get("chapter") else "Oneshot"
                if a.get("title"):
                    label += f": {a['title']}"
                found[num] = Chapter(num, label, c["id"])
        return [found[n] for n in sorted(found)]

    def pages(self, chapter):
        body = self.get(f"{self.api}/at-home/server/{chapter.id}").json()
        base, ch = body["baseUrl"], body["chapter"]
        return [f"{base}/data/{ch['hash']}/{f}" for f in ch["data"]]


class WeebCentral(Source):
    name = "WeebCentral"
    base = "https://weebcentral.com"

    def search(self, query):
        params = {
            "text": query,
            "sort": "Best Match",
            "order": "Descending",
            "official": "Any",
            "anime": "Any",
            "adult": "Any",
            "display_mode": "Minimal Display",
            "limit": 10,
            "offset": 0,
        }
        html = self.get(f"{self.base}/search/data", params=params,
                        headers={"HX-Request": "true"}).text
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for art in soup.select("article"):
            a = art.select_one('a[href*="/series/"]')
            if not a:
                continue
            title = a.get("data-tip") or a.get_text(strip=True)
            meta = art.select_one("section")
            info = " · ".join(d.get_text(strip=True) for d in meta.find_all("div")) if meta else ""
            results.append(Series(self.name, a["href"], title, info))
        return results

    def chapters(self, series):
        # series.id is ".../series/<ID>/<slug>"; the full list lives at /series/<ID>/full-chapter-list
        series_id = re.search(r"/series/([^/]+)", series.id).group(1)
        html = self.get(f"{self.base}/series/{series_id}/full-chapter-list").text
        soup = BeautifulSoup(html, "html.parser")
        found = {}
        for a in soup.select('a[href*="/chapters/"]'):
            chapter_id = a["href"].rstrip("/").split("/")[-1]
            name = a.select_one("span.grow > span")
            label = name.get_text(strip=True) if name else a.get_text(" ", strip=True)
            num = chapter_number(label)
            if num not in found:
                found[num] = Chapter(num, label.replace("# ", "Ch. "), chapter_id)
        return [found[n] for n in sorted(found)]

    def pages(self, chapter):
        params = {"is_prev": "False", "current_page": 1, "reading_style": "long_strip"}
        html = self.get(f"{self.base}/chapters/{chapter.id}/images", params=params,
                        headers={"HX-Request": "true"}).text
        soup = BeautifulSoup(html, "html.parser")
        return [img["src"] for img in soup.find_all("img")
                if img.get("src", "").startswith("http") and img.get("alt", "").startswith("Page")]


class Hentai2Read(Source):
    name = "Hentai2Read"
    base = "https://hentai2read.com"

    def _props(self, path, **kw):
        """The site embeds each page's data as JSON in <script data-page="app">."""
        html = self.get(self.base + path, **kw).text
        m = re.search(r'<script data-page="app" type="application/json">(.*?)</script>', html, re.S)
        if not m:
            raise RuntimeError(f"{self.name}: page data not found at {path} (site layout changed?)")
        return json.loads(m.group(1))["props"]

    def search(self, query):
        books = self._props("/search", params={"q": query})["books"]["data"]
        return [
            Series(self.name, b["url"], b["title"],
                   f"{b.get('typeLabel', '')} · {b.get('chapters', '?')} ch · {b.get('pageCount', '?')} pages")
            for b in books[:10]
        ]

    def chapters(self, series):
        props = self._props(series.id)
        found = {}
        for c in props["chapters"]:
            if c.get("isMembers"):
                continue  # members-only chapters need a login
            num = chapter_number(c.get("number"))
            if num not in found:
                found[num] = Chapter(num, f"Ch. {c['number']}: {c['title']}", c["url"],
                                     referer=self.base + "/")
        return [found[n] for n in sorted(found)]

    def pages(self, chapter):
        return self._props(chapter.id)["images"]


SOURCES = [MangaDex, WeebCentral, Hentai2Read]
