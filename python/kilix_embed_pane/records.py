"""Cells as the records neuronte-display's Renderer draws: (scalar, fg, bg, flags).

Flags there: 1 bold, 2 italic, 4 underline, 8 strike, 16 inverse, 32 faint,
64 invisible, 128 overline, 256 a cluster the renderer cannot draw whole.
Kilix has already applied reverse video and the selection to the colours,
so 16 is never set here; callers use it for their own cursor.
"""

import unicodedata

from .kepmap import (
    ATTR_BOLD, ATTR_DIM, ATTR_INVISIBLE, ATTR_ITALIC, ATTR_OVERLINE, ATTR_STRIKE, ATTR_WIDE_TAIL, Frame,
)


def cell_text(frame: Frame, index: int) -> str:
    cell = frame.cells[index]
    if cell.attrs & ATTR_WIDE_TAIL:
        return ''
    text = chr(cell.ch) if cell.ch else ' '
    if cell.extra and cell.extra < len(frame.combining):
        n = frame.combining[cell.extra]
        text += ''.join(chr(c) for c in frame.combining[cell.extra + 1:cell.extra + 1 + n])
    return text


def to_records(frame: Frame) -> list:
    out = []
    for index, cell in enumerate(frame.cells):
        flags = 0
        a = cell.attrs
        if a & ATTR_BOLD:
            flags |= 1
        if a & ATTR_ITALIC:
            flags |= 2
        if cell.underline:
            flags |= 4
        if a & ATTR_STRIKE:
            flags |= 8
        if a & ATTR_DIM:
            flags |= 32
        if a & ATTR_INVISIBLE:
            flags |= 64
        if a & ATTR_OVERLINE:
            flags |= 128
        if a & ATTR_WIDE_TAIL:
            scalar = 32
        elif cell.extra:
            text = unicodedata.normalize('NFC', cell_text(frame, index))
            scalar = ord(text[0])
            if len(text) > 1:
                flags |= 256
        else:
            scalar = cell.ch or 32
        out.append((scalar, cell.fg, cell.bg, flags))
    return out
