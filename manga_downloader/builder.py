"""Turn downloaded chapter images into a device-sized EPUB (Kindle/KOReader) or PDF (reMarkable)."""

import io
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from PIL import Image


@dataclass
class Device:
    key: str
    name: str
    width: int  # screen pixels, portrait
    height: int
    dpi: int
    fmt: str  # "epub" or "pdf"


DEVICES = {
    "kindle": Device("kindle", "Kindle Paperwhite", 1236, 1648, 300, "epub"),
    "rm2": Device("rm2", "reMarkable 2", 1404, 1872, 226, "pdf"),
}

JPEG_QUALITY = 80  # e-ink shows 16 grays; higher quality only grows the file


def fit_page(img, device):
    """Grayscale, rotate spreads, split long strips, then pad each piece to the device's aspect ratio.

    Returns a list of JPEG bytes (usually one; several for a tall webtoon strip).
    """
    img = img.convert("L")
    w, h = img.size

    # landscape double-page spread: rotate so it fills the portrait screen
    if w > h:
        img = img.rotate(90, expand=True)
        w, h = img.size

    # webtoon strip much taller than the screen: cut into screen-shaped slices
    pieces = [img]
    slice_h = round(w * device.height / device.width)
    if h > 2 * slice_h:
        pieces = [img.crop((0, top, w, min(top + slice_h, h))) for top in range(0, h, slice_h)]

    out = []
    for piece in pieces:
        pw, ph = piece.size
        # only shrink: the reader upscales on screen for free, upscaling here just bloats the file
        scale = min(device.width / pw, device.height / ph, 1.0)
        if scale < 1:
            piece = piece.resize((max(1, round(pw * scale)), max(1, round(ph * scale))), Image.LANCZOS)
            pw, ph = piece.size
        # pad with white to the screen's aspect ratio so the page fills the screen exactly
        if pw / ph > device.width / device.height:
            cw, ch = pw, round(pw * device.height / device.width)
        else:
            cw, ch = round(ph * device.width / device.height), ph
        page = Image.new("L", (cw, ch), 255)
        page.paste(piece, ((cw - pw) // 2, (ch - ph) // 2))
        buf = io.BytesIO()
        page.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
        out.append(buf.getvalue())
    return out


# ---------- EPUB ----------

def write_epub(path, title, chapters, device):
    """chapters: list of (chapter_label, [jpeg_bytes, ...]) in reading order."""
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    W, H = device.width, device.height

    pages = []  # (page_name, chapter_label or None if not a chapter start)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # EPUB rule: "mimetype" must be the first entry and stored uncompressed
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
            '</container>\n'))

        n = 0
        for label, images in chapters:
            for i, jpeg in enumerate(images):
                n += 1
                name = f"p{n:05d}"
                # JPEGs are already compressed; storing them saves time
                z.writestr(f"OEBPS/images/{name}.jpg", jpeg, compress_type=zipfile.ZIP_STORED)
                z.writestr(f"OEBPS/pages/{name}.xhtml", (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                    f'<head><title>{escape(label)}</title>'
                    f'<meta name="viewport" content="width={W}, height={H}"/>'
                    '<style>html,body{margin:0;padding:0}img{display:block;width:100%;height:100%;object-fit:contain}</style>'
                    '</head>\n'
                    f'<body><img src="../images/{name}.jpg" alt=""/></body>\n</html>\n'))
                pages.append((name, label if i == 0 else None))

        toc = [(name, label) for name, label in pages if label]

        # EPUB3 navigation document
        nav_items = "\n".join(f'<li><a href="pages/{n_}.xhtml">{escape(l)}</a></li>' for n_, l in toc)
        z.writestr("OEBPS/nav.xhtml", (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
            f'<head><title>{escape(title)}</title></head>\n'
            f'<body><nav epub:type="toc"><ol>\n{nav_items}\n</ol></nav></body>\n</html>\n'))

        # EPUB2 NCX, which some readers (incl. KOReader's engine) still use for the TOC
        nav_points = "\n".join(
            f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>{escape(l)}</text></navLabel>'
            f'<content src="pages/{n_}.xhtml"/></navPoint>'
            for i, (n_, l) in enumerate(toc, 1))
        z.writestr("OEBPS/toc.ncx", (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n'
            f'<docTitle><text>{escape(title)}</text></docTitle>\n'
            f'<navMap>\n{nav_points}\n</navMap>\n</ncx>\n'))

        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        ]
        for i, (name, _) in enumerate(pages):
            cover = ' properties="cover-image"' if i == 0 else ""
            manifest.append(f'<item id="i{name}" href="images/{name}.jpg" media-type="image/jpeg"{cover}/>')
            manifest.append(f'<item id="{name}" href="pages/{name}.xhtml" media-type="application/xhtml+xml"/>')
        spine = "\n".join(f'<itemref idref="{name}"/>' for name, _ in pages)

        # pre-paginated = fixed layout: one image per screen, no reflow
        z.writestr("OEBPS/content.opf", (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n'
            f'<dc:title>{escape(title)}</dc:title>\n'
            '<dc:language>en</dc:language>\n'
            f'<meta property="dcterms:modified">{modified}</meta>\n'
            '<meta property="rendition:layout">pre-paginated</meta>\n'
            '<meta property="rendition:spread">none</meta>\n'
            f'<meta name="cover" content="i{pages[0][0]}"/>\n'
            '</metadata>\n'
            f'<manifest>\n' + "\n".join(manifest) + '\n</manifest>\n'
            f'<spine toc="ncx">\n{spine}\n</spine>\n'
            '</package>\n'))


# ---------- PDF ----------

def _pdf_text(s):
    """Encode a string as a PDF UTF-16 text string (works for any title)."""
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def write_pdf(path, title, chapters, device):
    """Minimal PDF writer: embeds the JPEGs as-is (no re-encode) with a chapter outline."""
    W, H = device.width, device.height
    # page size in points at the device's real DPI, so it opens at true physical size
    pw, ph = W * 72 / device.dpi, H * 72 / device.dpi

    offsets = []  # byte offset of each object, index = object number - 1
    with open(path, "wb") as f:
        def obj(body):
            """Write the next object; body is bytes (may contain a stream)."""
            offsets.append(f.tell())
            f.write(f"{len(offsets)} 0 obj\n".encode() + body + b"\nendobj\n")
            return len(offsets)

        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        # object numbers are reserved up front: 1 catalog, 2 page tree, 3 outline root, 4 info
        n_pages = sum(len(imgs) for _, imgs in chapters)
        first_page_obj = 5  # each page uses 3 objects: page, content, image
        page_ids = [first_page_obj + 3 * i for i in range(n_pages)]
        first_outline_obj = first_page_obj + 3 * n_pages

        # chapter starts for the outline
        starts, n = [], 0
        for label, imgs in chapters:
            if imgs:
                starts.append((label, page_ids[n]))
            n += len(imgs)
        outline_ids = [first_outline_obj + i for i in range(len(starts))]

        obj(b"<< /Type /Catalog /Pages 2 0 R /Outlines 3 0 R /PageMode /UseOutlines >>")
        kids = " ".join(f"{p} 0 R" for p in page_ids)
        obj(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
        if outline_ids:
            obj(f"<< /Type /Outlines /First {outline_ids[0]} 0 R /Last {outline_ids[-1]} 0 R "
                f"/Count {len(outline_ids)} >>".encode())
        else:
            obj(b"<< /Type /Outlines /Count 0 >>")
        obj(f"<< /Title {_pdf_text(title)} /Producer (manga_builder) >>".encode())

        for (_, imgs) in chapters:
            for jpeg in imgs:
                pid = len(offsets) + 1
                obj(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
                    f"/Resources << /XObject << /Im0 {pid + 2} 0 R >> >> /Contents {pid + 1} 0 R >>".encode())
                # draw the image over the whole page
                draw = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q".encode()
                obj(f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream")
                w, h = Image.open(io.BytesIO(jpeg)).size
                obj(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                    f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n".encode()
                    + jpeg + b"\nendstream")

        # outline entries form a doubly linked list under object 3
        for i, (label, page) in enumerate(starts):
            links = ""
            if i > 0:
                links += f" /Prev {outline_ids[i - 1]} 0 R"
            if i < len(starts) - 1:
                links += f" /Next {outline_ids[i + 1]} 0 R"
            obj(f"<< /Title {_pdf_text(label)} /Parent 3 0 R{links} /Dest [{page} 0 R /Fit] >>".encode())

        xref = f.tell()
        f.write(f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode())
        for off in offsets:
            f.write(f"{off:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R /Info 4 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n".encode())
