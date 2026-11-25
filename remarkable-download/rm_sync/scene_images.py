"""Decode capture asset tables (0x0e v3) and image items (0x0f v2).

These records are unreadable blocks in rmscene 0.8. Keep the adapter local;
the upstream scene tree still resolves ordering, tombstones and visibility.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from io import BytesIO
import math

from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.tagged_block_common import CrdtId
from rmscene.tagged_block_reader import TaggedBlockReader

from models import SceneImage


@dataclass
class ImageBlock:
    parent_id: CrdtId
    item: CrdtSequenceItem


@contextmanager
def exact_subblock(reader, index):
    # Reject new layouts rather than silently dropping their geometry or flags.
    with reader.read_subblock(index) as info:
        yield info
    if info.extra_data:
        raise ValueError(f"Unexpected data in image subblock {index}")


def read_assets(reader):
    assets = {}
    with exact_subblock(reader, 1):
        count = reader.data.read_varuint()
        for _ in range(count):
            with exact_subblock(reader, 0):
                asset_id = reader.data.read_bytes(16)
                with exact_subblock(reader, 1):
                    reader.read_id(1)  # Filename timestamp.
                    filename = reader.read_string(2)
                with exact_subblock(reader, 2):
                    reader.read_id(1)
                    with exact_subblock(reader, 2):
                        # The supplied capture uses this zero-valued asset flag.
                        if reader.read_byte(1) != 0:
                            raise ValueError("Unsupported image asset flag")
                if not filename.endswith('.png') or '/' in filename or '\\' in filename:
                    raise ValueError("Invalid image asset filename")
                assets[asset_id] = filename
    return assets


def read_image(reader, block_index):
    parent_id = reader.read_id(1)
    item_id = reader.read_id(2)
    left_id = reader.read_id(3)
    right_id = reader.read_id(4)
    deleted_length = reader.read_int(5)
    image = None
    if reader.has_subblock(6):
        with exact_subblock(reader, 6):
            if reader.data.read_uint8() != 7:
                raise ValueError("Unexpected image item type")
            with exact_subblock(reader, 1):
                reader.read_id(1)  # Asset-reference timestamp.
                with exact_subblock(reader, 2):
                    asset_id = reader.data.read_bytes(16)
            reader.read_id(2)  # Placement/move ID.
            with exact_subblock(reader, 3) as info:
                count = reader.data.read_varuint()
                if count % 4 or reader.data.tell() + count * 4 != info.offset + info.size:
                    raise ValueError("Invalid image vertex array")
                vertices = [tuple(reader.data.read_float32() for _ in range(4))
                            for _ in range(count // 4)]
            with exact_subblock(reader, 4) as info:
                count = reader.data.read_varuint()
                if count % 3 or reader.data.tell() + count * 4 != info.offset + info.size:
                    raise ValueError("Invalid image triangle array")
                triangles = [tuple(reader.data.read_uint32() for _ in range(3))
                             for _ in range(count // 3)]
            # Pasted/moved snippets reference an earlier (deleted) image item.
            reader.read_id_optional(5)
            if not vertices or not triangles or not all(math.isfinite(v) for p in vertices for v in p):
                raise ValueError("Invalid image mesh")
            for triangle in triangles:
                if any(i >= len(vertices) for i in triangle):
                    raise ValueError("Image triangle index out of bounds")
                a, b, c = (vertices[i] for i in triangle)
                area = (b[2] - a[2]) * (c[3] - a[3]) - (c[2] - a[2]) * (b[3] - a[3])
                if abs(area) < 1e-12:
                    raise ValueError("Degenerate image UV triangle")
            image = SceneImage(asset_id, vertices, triangles, block_index)
    return ImageBlock(parent_id, CrdtSequenceItem(item_id, left_id, right_id, deleted_length, image))


def decode_image_blocks(blocks):
    """Return supported indices, image items, and page-local asset filenames."""
    supported, images, assets = set(), {}, {}
    for index, block in enumerate(blocks):
        if block.__class__.__name__ != 'UnreadableBlock':
            continue
        info = getattr(block, 'info', None)
        kind = getattr(info, 'block_type', None)
        if kind not in (14, 15):
            continue
        try:
            expected = (3, 3) if kind == 14 else (2, 2)
            if (info.min_version, info.current_version) != expected:
                raise ValueError("Unverified image block version")
            reader = TaggedBlockReader(BytesIO(block.data))
            reader.current_block = replace(info, offset=0, size=len(block.data))
            if kind == 14:
                assets.update(read_assets(reader))
            else:
                images[index] = read_image(reader, index)
            if reader.bytes_remaining_in_block() != 0 or block.extra_data:
                raise ValueError("Unexpected data after image block")
            supported.add(index)
        except Exception as exc:
            raise ValueError(f"Unsupported scene block {index}: {exc}") from exc
    return supported, images, assets
