"""Offline tests: run with  python -m pytest test_manga_builder.py"""

import io
import json
import zipfile
import xml.dom.minidom
from dataclasses import replace

import pytest
import requests
from PIL import Image

import manga_builder
from builder import DEVICES, fit_page, write_epub, write_pdf
from sources import Chapter, Hentai2Read, MangaDex, Series, WeebCentral, chapter_number


# ---------- helpers ----------

class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body

    @property
    def text(self):
        return self.body

    @property
    def content(self):
        return self.body


class FakeSession:
    """Returns canned bodies; `routes` maps a URL substring to a body or a list of bodies (served in order)."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if key in url:
                return FakeResponse(body.pop(0) if isinstance(body, list) else body)
        raise AssertionError(f"unexpected URL {url}")


def jpeg(w, h, color=128):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (color, color, color)).save(buf, "JPEG")
    return buf.getvalue()


def chapters_of(*nums):
    return [Chapter(n, f"Ch. {n}", str(n)) for n in nums]


# ---------- chapter selection ----------

@pytest.mark.parametrize("text, expected", [
    ("all", [1, 2, 3, 4, 5, 5.5, 6, 10]),
    ("2-4", [2, 3, 4]),
    ("5-6", [5, 5.5, 6]),  # decimals inside a range are included
    ("5.5", [5.5]),
    ("1, 3, 6-10", [1, 3, 6, 10]),
    ("4-2", [2, 3, 4]),  # reversed range still works
    ("99", []),
])
def test_parse_selection(text, expected):
    chapters = chapters_of(1, 2, 3, 4, 5, 5.5, 6, 10)
    assert [c.number for c in manga_builder.parse_selection(text, chapters)] == expected


def test_parse_selection_rejects_garbage():
    with pytest.raises(ValueError):
        manga_builder.parse_selection("one-two", chapters_of(1))


@pytest.mark.parametrize("label, num", [("# 134", 134), ("Vol 2 Chapter 10.5", 10.5), ("Oneshot", 0)])
def test_chapter_number(label, num):
    assert chapter_number(label) == num


# ---------- MangaDex ----------

def md_chapter(cid, num, pages=20, title=None):
    return {"id": cid, "attributes": {"chapter": num, "title": title, "pages": pages}}


def test_mangadex_chapters_dedupes_paginates_and_sorts():
    feed = [
        {"total": 4, "data": [md_chapter("a", "1"), md_chapter("b", "1"), md_chapter("c", "2", title="Hi")]},
        {"total": 4, "data": [md_chapter("d", "1.5"), md_chapter("e", "3", pages=0)]},
    ]
    md = MangaDex(FakeSession({"/feed": feed}))
    chapters = md.chapters(Series("MangaDex", "m1", "T"))
    # duplicate ch. 1 keeps the first upload; 0-page chapter dropped; sorted numerically
    assert [(c.number, c.id) for c in chapters] == [(1, "a"), (1.5, "d"), (2, "c")]
    assert chapters[2].label == "Ch. 2: Hi"


def test_mangadex_search_prefers_english_title():
    body = {"data": [
        {"id": "x", "attributes": {"title": {"ja-ro": "Romaji"}, "altTitles": [{"en": "English"}],
                                   "year": 2020, "status": "ongoing"}},
    ]}
    session = FakeSession({"/manga": body})
    [s] = MangaDex(session).search("q")
    assert (s.id, s.title, s.info) == ("x", "English", "2020 · ongoing")
    # only series that actually have English chapters are requested
    assert session.calls[0][1]["availableTranslatedLanguage[]"] == ["en"]


def test_mangadex_pages():
    body = {"baseUrl": "https://s.net", "chapter": {"hash": "h", "data": ["1.png", "2.png"]}}
    md = MangaDex(FakeSession({"/at-home/server/c1": body}))
    assert md.pages(Chapter(1, "Ch. 1", "c1")) == ["https://s.net/data/h/1.png", "https://s.net/data/h/2.png"]


# ---------- WeebCentral (HTML in the shape the site returns) ----------

WC_SEARCH = """
<article class="bg-base-300"><a href="https://weebcentral.com/series/01ABC/Some-Title" data-tip="Some Title">
<h2>Some Title</h2></a><section class="flex"><div>Manga</div>&middot;<div>2013</div>&middot;<div>Complete</div></section>
<section><strong>Tag(s): </strong><span>Action</span></section></article>
"""

WC_LIST = """
<div><a href="/chapters/ID3" class="x"><span class="me-2"><img src="/badge.svg"></span>
<span class="grow flex"><span class=""># 3</span><span x-show="x">Last Read</span></span></a></div>
<div><a href="/chapters/ID2b"><span class="grow"><span class=""># 2.5</span></span></a></div>
<div><a href="/chapters/ID1"><span class="grow"><span class=""># 1</span></span></a></div>
"""

WC_IMAGES = """
<img src="https://cdn.example/0003-001.png" alt="Page 1">
<img src="https://cdn.example/0003-002.png" alt="Page 2">
<img src="/static/logo.png" alt="logo">
"""


def test_weebcentral_search_chapters_pages():
    wc = WeebCentral(FakeSession({"/search/data": WC_SEARCH, "/full-chapter-list": WC_LIST,
                                  "/chapters/ID3/images": WC_IMAGES}))
    [s] = wc.search("some")
    assert (s.title, s.info) == ("Some Title", "Manga · 2013 · Complete")

    chapters = wc.chapters(s)
    assert [(c.number, c.id, c.label) for c in chapters] == [
        (1, "ID1", "Ch. 1"), (2.5, "ID2b", "Ch. 2.5"), (3, "ID3", "Ch. 3")]
    assert wc.session.calls[-1][0] == "https://weebcentral.com/series/01ABC/full-chapter-list"

    assert wc.pages(chapters[2]) == ["https://cdn.example/0003-001.png", "https://cdn.example/0003-002.png"]


# ---------- Hentai2Read (JSON embedded in the page) ----------

def h2r_page(props):
    return f'<html><script data-page="app" type="application/json">{json.dumps({"props": props})}</script></html>'


def test_hentai2read_search_chapters_pages():
    search = h2r_page({"books": {"data": [
        {"title": "Book", "url": "/original/1-book", "typeLabel": "Original", "chapters": 2, "pageCount": 40}]}})
    book = h2r_page({"chapters": [  # site lists newest first
        {"number": "2", "title": "Two", "url": "/reader/1-book/12/page-1", "isMembers": False},
        {"number": "1.5", "title": "Locked", "url": "/reader/1-book/11/page-1", "isMembers": True},
        {"number": "1", "title": "One", "url": "/reader/1-book/10/page-1", "isMembers": False}]})
    reader = h2r_page({"images": ["https://cdn.example/1/1/001.jpg", "https://cdn.example/1/1/002.jpg"]})
    h = Hentai2Read(FakeSession({"/search": search, "/original/1-book": book, "/reader/": reader}))

    [s] = h.search("book")
    assert (s.id, s.info) == ("/original/1-book", "Original · 2 ch · 40 pages")

    chapters = h.chapters(s)
    # ascending, members-only chapter skipped
    assert [(c.number, c.label) for c in chapters] == [(1, "Ch. 1: One"), (2, "Ch. 2: Two")]
    assert chapters[0].referer == "https://hentai2read.com/"
    assert h.pages(chapters[0]) == ["https://cdn.example/1/1/001.jpg", "https://cdn.example/1/1/002.jpg"]


def test_hentai2read_layout_change_is_a_clear_error():
    h = Hentai2Read(FakeSession({"/search": "<html>new layout</html>"}))
    with pytest.raises(RuntimeError, match="page data not found"):
        h.search("x")


# ---------- page fitting ----------

def size_of(jpeg_bytes):
    with Image.open(io.BytesIO(jpeg_bytes)) as im:
        return im.size, im.mode


def aspect_matches(size, device):
    (w, h) = size
    return abs(w / h - device.width / device.height) < 0.002


@pytest.mark.parametrize("device", DEVICES.values(), ids=DEVICES.keys())
def test_fit_page_large_page_shrinks_to_screen(device):
    [page] = fit_page(Image.new("RGB", (3000, 4000)), device)
    assert size_of(page) == ((device.width, device.height), "L")


@pytest.mark.parametrize("device", DEVICES.values(), ids=DEVICES.keys())
def test_fit_page_small_page_not_upscaled_but_screen_shaped(device):
    [page] = fit_page(Image.new("RGB", (900, 1400)), device)
    (w, h), mode = size_of(page)
    assert mode == "L" and h == 1400 and w >= 900  # padded sideways, not enlarged
    assert aspect_matches((w, h), device)


def test_kindle_profile_is_users_paperwhite():
    k = DEVICES["kindle"]
    assert (k.width, k.height, k.cap, k.quality) == (1072, 1448, 1448, 70)
    r = DEVICES["rm2"]
    assert (r.width, r.height, r.cap, r.quality) == (1404, 1872, 1872, 70)


def test_fit_page_source_sized_page_fits_kindle_screen():
    # a typical 1200x1600 source page ends up exactly at the Kindle screen size
    [page] = fit_page(Image.new("RGB", (1200, 1600)), DEVICES["kindle"])
    assert size_of(page)[0] == (1072, 1448)


def test_fit_page_lower_cap_shrinks_more():
    dev = replace(DEVICES["kindle"], cap=1000)
    [page] = fit_page(Image.new("RGB", (1200, 1600)), dev)
    (w, h), _ = size_of(page)
    assert h == 1000 and aspect_matches((w, h), dev)


def test_fit_page_uncapped_keeps_source_resolution():
    dev = replace(DEVICES["kindle"], cap=None)
    [page] = fit_page(Image.new("RGB", (1200, 1600)), dev)
    (w, h), _ = size_of(page)
    # 1200x1600 is a bit wider than the Kindle's shape, so it's padded top/bottom: full width kept
    assert w == 1200 and h >= 1600 and aspect_matches((w, h), dev)


def test_fit_page_quality_changes_file_size():
    # noisy image so JPEG quality actually matters
    img = Image.effect_noise((600, 800), 60)
    [low] = fit_page(img, replace(DEVICES["kindle"], quality=40))
    [high] = fit_page(img, replace(DEVICES["kindle"], quality=95))
    assert len(low) < len(high)


@pytest.mark.parametrize("text, expected", [
    ("cap1448-q70", (1448, 70)),
    ("cap1000-70", (1000, 70)),
    ("uncap-100", (None, 100)),
    ("UNCAP-Q90", (None, 90)),
    (" cap1872-q70 ", (1872, 70)),
])
def test_parse_quality(text, expected):
    assert manga_builder.parse_quality(text) == expected


@pytest.mark.parametrize("text", ["cap-70", "uncap", "cap1448", "cap1448-q0", "cap1448-q101", "cap0-q70", "foo"])
def test_parse_quality_rejects_bad_input(text):
    with pytest.raises(ValueError):
        manga_builder.parse_quality(text)


def test_pick_quality_default_custom_and_retry(monkeypatch):
    answers = iter(["", "nope", "uncap-100"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    kindle = DEVICES["kindle"]
    assert manga_builder.pick_quality(kindle) == kindle  # Enter keeps cap1448-q70
    custom = manga_builder.pick_quality(kindle)  # bad answer re-asked, then uncap-100
    assert (custom.cap, custom.quality) == (None, 100)


def test_fit_page_rotates_spread():
    # wide spread with a black left half; after a 90° CCW rotation the black half is at the bottom
    img = Image.new("L", (2000, 1400), 255)
    img.paste(0, (0, 0, 1000, 1400))
    [page] = fit_page(img, DEVICES["kindle"])
    with Image.open(io.BytesIO(page)) as im:
        w, h = im.size
        assert im.getpixel((w // 2, h // 2 - 300)) > 200  # top half white
        assert im.getpixel((w // 2, h // 2 + 300)) < 50  # bottom half black


def test_fit_page_splits_long_strip():
    pages = fit_page(Image.new("RGB", (800, 800 * 6)), DEVICES["rm2"])
    assert len(pages) >= 4
    assert all(aspect_matches(size_of(p)[0], DEVICES["rm2"]) for p in pages)


# ---------- EPUB ----------

def test_write_epub(tmp_path):
    out = tmp_path / "book.epub"
    chapters = [("Ch. 1: A & B", [jpeg(100, 150)] * 2), ("Ch. 2", [jpeg(100, 150)])]
    write_epub(out, "Title <1>", chapters, DEVICES["kindle"])

    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        # every XML file parses (catches escaping mistakes)
        for name in z.namelist():
            if name.endswith((".xml", ".opf", ".xhtml", ".ncx")):
                xml.dom.minidom.parseString(z.read(name))
        opf = z.read("OEBPS/content.opf").decode()
        assert "pre-paginated" in opf and "<dc:language>en</dc:language>" in opf
        assert opf.count("<itemref ") == 3
        nav = z.read("OEBPS/nav.xhtml").decode()
        assert "Ch. 1: A &amp; B" in nav and 'href="pages/p00003.xhtml">Ch. 2' in nav
        assert len([n for n in z.namelist() if n.startswith("OEBPS/images/")]) == 3


# ---------- PDF ----------

def test_write_pdf_opens_with_right_pages_and_outline(tmp_path):
    fitz = pytest.importorskip("fitz")
    out = tmp_path / "book.pdf"
    dev = DEVICES["rm2"]
    chapters = [("Ch. 1", [jpeg(1404, 1872, 0)] * 2), ("Ch. 2 – ünïcode", [jpeg(1404, 1872, 255)])]
    write_pdf(out, "Title", chapters, dev)

    doc = fitz.open(out)
    assert doc.page_count == 3
    assert not doc.is_repaired  # xref table was valid
    # page has the reMarkable 2 aspect ratio
    r = doc[0].rect
    assert abs(r.width / r.height - dev.width / dev.height) < 0.001
    assert doc.get_toc() == [[1, "Ch. 1", 1], [1, "Ch. 2 – ünïcode", 3]]
    # the image really is drawn: page 1 is black, page 3 white
    assert doc[0].get_pixmap(dpi=20).pixel(5, 5)[0] < 30
    assert doc[2].get_pixmap(dpi=20).pixel(5, 5)[0] > 225


# ---------- downloading ----------

class FakeSource:
    name = "Fake"

    def __init__(self, urls, bodies):
        self.urls, self.bodies, self.fetched = urls, bodies, []

    def pages(self, chapter):
        return self.urls

    def get(self, url, headers=None):
        self.fetched.append(url)
        body = self.bodies[url]
        if isinstance(body, list):
            body = body.pop(0)
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body)


def test_download_resumes_and_refetches_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(manga_builder, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(manga_builder.time, "sleep", lambda s: None)
    urls = ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"]
    good = jpeg(50, 70)
    src = FakeSource(urls, {
        urls[0]: good,
        urls[1]: [good[:100], good],  # first try truncated -> retried
        urls[2]: [requests.ConnectionError("boom"), good],
    })
    series, ch = Series("Fake", "s", "My/Series"), Chapter(1, "Ch. 1", "c")

    paths = manga_builder.download_chapter(src, series, ch)
    assert [p.name for p in paths] == ["001.jpg", "002.jpg", "003.jpg"]
    assert paths[0].parent == tmp_path / "Fake" / "My_Series" / "ch-1"
    assert len(src.fetched) == 5

    # second run: everything valid on disk, nothing re-fetched
    src.fetched.clear()
    manga_builder.download_chapter(src, series, ch)
    assert src.fetched == []

    # a corrupted file on disk gets downloaded again
    paths[1].write_bytes(b"garbage")
    src.bodies[urls[1]] = good
    manga_builder.download_chapter(src, series, ch)
    assert src.fetched == [urls[1]]


def test_download_retries_page_list_on_server_error(tmp_path, monkeypatch):
    monkeypatch.setattr(manga_builder, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(manga_builder.time, "sleep", lambda s: None)
    urls = ["https://x/1.jpg"]
    src = FakeSource(urls, {urls[0]: jpeg(50, 70)})
    calls = []

    def flaky_pages(chapter):  # 500 once, then fine (what MangaDex's at-home endpoint did)
        calls.append(1)
        if len(calls) == 1:
            raise requests.HTTPError("500 Server Error")
        return urls

    src.pages = flaky_pages
    paths = manga_builder.download_chapter(src, Series("Fake", "s", "S"), Chapter(1, "Ch. 1", "c"))
    assert len(calls) == 2 and [p.name for p in paths] == ["001.jpg"]

    # still failing after all retries -> the error surfaces (build() then skips the chapter)
    src.pages = lambda ch: (_ for _ in ()).throw(requests.HTTPError("500"))
    with pytest.raises(requests.HTTPError):
        manga_builder.download_chapter(src, Series("Fake", "s", "S"), Chapter(2, "Ch. 2", "c2"))


def test_build_writes_each_chunk_before_downloading_the_next(tmp_path, monkeypatch):
    monkeypatch.setattr(manga_builder, "DOWNLOAD_DIR", tmp_path / "dl")
    monkeypatch.setattr(manga_builder, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(manga_builder, "CHUNK_SIZE", 2)
    url = "https://x/1.jpg"
    src = FakeSource([url], {url: jpeg(50, 70)})
    seen = []  # output files present when each chapter starts downloading

    def pages(chapter):
        seen.append((chapter.number, sorted(p.name for p in (tmp_path / "out").glob("*"))))
        return [url]

    src.pages = pages
    outputs = manga_builder.build(src, Series("Fake", "s", "S"), chapters_of(1, 2, 3, 4, 5),
                                  list(DEVICES.values()))

    names = [p.name for p in outputs]
    assert names == [
        "S - Ch. 1-2 (Kindle Paperwhite).epub", "S - Ch. 1-2 (reMarkable 2).pdf",
        "S - Ch. 3-4 (Kindle Paperwhite).epub", "S - Ch. 3-4 (reMarkable 2).pdf",
        "S - Ch. 5 (Kindle Paperwhite).epub", "S - Ch. 5 (reMarkable 2).pdf",
    ]
    # chunk 1's files already exist when chapter 3 starts; chunk 2's when chapter 5 starts
    assert seen[2] == (3, sorted(names[:2]))
    assert seen[4] == (5, sorted(names[:4]))


def test_download_skips_page_that_keeps_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(manga_builder, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(manga_builder.time, "sleep", lambda s: None)
    urls = ["https://x/1.jpg", "https://x/2.jpg"]
    src = FakeSource(urls, {urls[0]: jpeg(50, 70), urls[1]: b"not an image"})
    paths = manga_builder.download_chapter(src, Series("Fake", "s", "S"), Chapter(1, "Ch. 1", "c"))
    assert [p.name for p in paths] == ["001.jpg"]
