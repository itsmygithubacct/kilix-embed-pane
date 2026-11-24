"""Reading the KEPM cell map (include/kep_map.h) an embedded window writes."""

import mmap
import os
import struct
from typing import NamedTuple

MAGIC = b'KEPM'
VERSION = 1
HEADER_SIZE = 256

# Field order and types exactly as kep_map_header.
_HEADER = struct.Struct('<4s3I3I3I4I6B2x2i3I2i3IQII128s8x')
_FIELDS = (
    'magic version header_size cell_size max_cols max_rows combining_capacity cells_offset dirty_offset '
    'combining_offset seq cols rows modes mouse_tracking_mode mouse_tracking_protocol cursor_shape '
    'cursor_visible cursor_blink cursor_focused cursor_x cursor_y cursor_color default_fg default_bg '
    'history_rows view_offset combining_len key_encoding_flags reserved1 frames child_pid title_len title'
).split()
assert _HEADER.size == HEADER_SIZE and len(_FIELDS) == len(_HEADER.unpack(bytes(HEADER_SIZE)))
_SEQ = 40  # byte offset of seq
CELL = struct.Struct('<6IHBB')
assert CELL.size == 28

ATTR_BOLD, ATTR_ITALIC, ATTR_DIM, ATTR_STRIKE = 1, 2, 4, 8
ATTR_BLINK, ATTR_INVISIBLE, ATTR_WIDE, ATTR_WIDE_TAIL = 16, 32, 64, 128
ATTR_SELECTED, ATTR_OVERLINE = 256, 512
COLOR_SAME_AS_FG = 0x01000000
MODE_BRACKETED_PASTE, MODE_FOCUS_TRACKING, MODE_ALTERNATE_SCREEN = 1, 2, 4
MODE_APPLICATION_CURSOR, MODE_REVERSE_VIDEO = 8, 16


class MapError(Exception):
    pass


class Cell(NamedTuple):
    ch: int
    extra: int
    fg: int
    bg: int
    ul_color: int
    link: int
    attrs: int
    underline: int
    reserved: int


class Frame(NamedTuple):
    cols: int
    rows: int
    cells: tuple           # Cell, rows * cols
    dirty: bytes           # one byte a row; all 1 when frames were missed
    combining: tuple       # see kep_grid.combining
    cursor: tuple          # (x, y, shape, visible, blink, focused, color)
    modes: int
    mouse_tracking_mode: int
    mouse_tracking_protocol: int
    default_fg: int
    default_bg: int
    history_rows: int
    view_offset: int
    key_encoding_flags: int
    frames: int
    title: str


class MapReader:
    """One embedded window's map, read-only. read() returns a Frame when the
    window changed since the last read, else None."""

    def __init__(self, path: str) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
        try:
            st = os.fstat(fd)
            if st.st_size < HEADER_SIZE:
                raise MapError('The map is smaller than its header')
            self._map = mmap.mmap(fd, st.st_size, prot=mmap.PROT_READ)
        finally:
            os.close(fd)
        h = self._header()
        if h['magic'] != MAGIC or h['version'] != VERSION or h['header_size'] != HEADER_SIZE \
                or h['cell_size'] != CELL.size:
            self.close()
            raise MapError('Not a version 1 KEPM map')
        end = h['combining_offset'] + 4 * h['combining_capacity']
        if h['cells_offset'] < HEADER_SIZE or h['dirty_offset'] < h['cells_offset'] + h['max_cols'] * h['max_rows'] * CELL.size \
                or h['combining_offset'] < h['dirty_offset'] + h['max_rows'] or end > len(self._map):
            self.close()
            raise MapError('The map layout does not fit the file')
        self.max_cols, self.max_rows = h['max_cols'], h['max_rows']
        self.last_frames = -1

    def _header(self) -> dict:
        return dict(zip(_FIELDS, _HEADER.unpack_from(self._map, 0)))

    def _seq(self) -> int:
        return struct.unpack_from('<I', self._map, _SEQ)[0]

    def read(self, force: bool = False, tries: int = 200):
        if self._map is None:
            raise MapError('The map is closed')
        for _ in range(tries):
            seq = self._seq()
            if seq & 1:
                continue
            h = self._header()
            if h['frames'] == 0:  # attached, no frame written yet
                if self._seq() == seq:
                    return None
                continue
            if not force and h['frames'] == self.last_frames:
                if self._seq() == seq:
                    return None
                continue
            cols, rows = h['cols'], h['rows']
            if not (0 < cols <= self.max_cols and 0 < rows <= self.max_rows) \
                    or h['combining_len'] > h['combining_capacity']:
                if self._seq() == seq:
                    raise MapError('The map header is out of range')
                continue
            raw = self._map[h['cells_offset']:h['cells_offset'] + cols * rows * CELL.size]
            dirty = self._map[h['dirty_offset']:h['dirty_offset'] + rows]
            comb = self._map[h['combining_offset']:h['combining_offset'] + 4 * h['combining_len']]
            if self._seq() != seq:
                continue
            missed = force or self.last_frames < 0 or h['frames'] != self.last_frames + 1
            self.last_frames = h['frames']
            cells = tuple(Cell(*CELL.unpack_from(raw, i * CELL.size)) for i in range(cols * rows))
            title = h['title'][:min(h['title_len'], 128)].decode('utf-8', 'replace')
            return Frame(
                cols, rows, cells, bytes([1] * rows) if missed else bytes(dirty),
                struct.unpack(f'<{h["combining_len"]}I', comb),
                (h['cursor_x'], h['cursor_y'], h['cursor_shape'], bool(h['cursor_visible']),
                 bool(h['cursor_blink']), bool(h['cursor_focused']), h['cursor_color']),
                h['modes'], h['mouse_tracking_mode'], h['mouse_tracking_protocol'], h['default_fg'],
                h['default_bg'], h['history_rows'], h['view_offset'], h['key_encoding_flags'], h['frames'], title)
        raise MapError('The map kept changing while being read')

    def close(self) -> None:
        if getattr(self, '_map', None) is not None:
            self._map.close()
            self._map = None

    def __enter__(self) -> 'MapReader':
        return self

    def __exit__(self, *a: object) -> None:
        self.close()
