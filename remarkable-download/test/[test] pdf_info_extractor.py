from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF


def pdf_info_extractor(path: Path):
    """
    Opens a PDF file at the given path and extracts and prints as much information as possible
    using PyMuPDF (fitz), for all doc-level and page-level attributes, with comments for each.
    """
    def safe_attr(obj, name, default="N/A"):
        return getattr(obj, name, default)

    def safe_call(obj, name, *args, default="N/A", **kwargs):
        fn = getattr(obj, name, None)
        if not callable(fn):
            return default
        try:
            return fn(*args, **kwargs)
        except Exception:
            return default

    with fitz.open(path) as doc:
        print(f"File: {path.name}")
   
        print(f"Encrypted: {safe_attr(doc, 'is_encrypted', 'Unknown')}")  # Whether the PDF is encrypted
        page_count = safe_attr(doc, "page_count", 0)
        print(f"Number of pages: {page_count}")
        metadata = safe_attr(doc, "metadata", {})
        print(f"Metadata: {metadata}")        # Document metadata dictionary
        # PyMuPDF does not expose `Document.pdf_version` in all versions.
        # The PDF header/version is typically available in metadata["format"].
        pdf_version = metadata.get("format") if isinstance(metadata, dict) else None
        print(f"PDF version: {pdf_version or 'Unknown'}")
        print(f"Is PDF/A: {safe_attr(doc, 'is_pdf', 'Unknown')}")
        # `is_linearized` is not available in every PyMuPDF release.
        linearized = getattr(doc, "is_linearized", None)
        if linearized is None:
            linearized = getattr(doc, "is_fast_webaccess", None)
        print(
            "Is linearized: "
            f"{linearized if linearized is not None else 'Unknown (not exposed by this PyMuPDF version)'}"
        )
        print(f"Table of contents: {safe_call(doc, 'get_toc', default=[])}")
        print(f"Form fields: {safe_call(doc, 'get_form_fields', default='N/A')}")
        print(f"Embedded files: {doc.embeddedFileCount if hasattr(doc,'embeddedFileCount') else 'N/A'}")

        xrefs = set()
        for i in range(page_count):
            page = doc.load_page(i)
            print(f"\nPage {i + 1}:")

            # Basic geometry information
            rect = page.rect                    # The default MediaBox
            print(f"  rect (MediaBox): {rect} (width={rect.width:.2f}pt, height={rect.height:.2f}pt)")

            # All box info if available
            try:
                mediabox = page.mediabox
                print(f"  MediaBox: {mediabox}")
            except AttributeError:
                pass
            try:
                cropbox = page.cropbox
                print(f"  CropBox: {cropbox} (usable area shown in most PDF viewers)")
            except AttributeError:
                pass
            try:
                bleedbox = page.bleedbox
                print(f"  BleedBox: {bleedbox}")
            except AttributeError:
                pass
            try:
                trimbox = page.trimbox
                print(f"  TrimBox: {trimbox}")
            except AttributeError:
                pass
            try:
                artbox = page.artbox
                print(f"  ArtBox: {artbox}")
            except AttributeError:
                pass

            # Page rotation (degrees)
            print(f"  Rotation: {page.rotation}")

            # Labels, if present
            try:
                label = page.get_label() if hasattr(page, "get_label") else None
                print(f"  Page label: {label}")
            except Exception:
                pass

            # Text and image content (counts)
            text = page.get_text()
            print(f"  Text length: {len(text)}")
            blocks = page.get_text("blocks")
            print(f"  Text blocks: {len(blocks)}")
            images = page.get_images(full=True)
            print(f"  Image count: {len(images)}")
            for j, img in enumerate(images, 1):
                xrefs.add(img[0])
                print(f"    Image {j}: xref={img[0]}, width={img[2]}, height={img[3]}, bpc={img[4]}, colorspace={img[5]}")

            # Annots and links
            annots = list(safe_call(page, "annots", default=[]) or [])
            print(f"  Annotation count: {len(annots)}")
            links = safe_call(page, "get_links", default=[]) or []
            print(f"  Link count: {len(links)}")
            # Layer info
            try:
                layer_count = len(page.get_layers() or [])
                print(f"  Layers: {layer_count}")
            except Exception:
                pass

            # Content streams
            try:
                nstreams = len(page.get_contents())
                print(f"  Number of content streams: {nstreams}")
            except Exception:
                pass

            # Additional info: resources, fonts, colorspaces, patterns, shadings
            # Not all PDFs will have these features; require more specialized inspection

        print(f"\nUnique image object xrefs (for extraction): {sorted(xrefs)}")

print("Correct version info")
for i in range(0,2):
    print("=" *60) 
path = Path("")
pdf_info_extractor(path)
path = Path("")
print("Renderred version info")
for i in range(0,2):
    print("=" *100)  
pdf_info_extractor(path)
