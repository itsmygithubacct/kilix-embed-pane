"""Writes KEPM maps the way the engine does, for tests (no engine needed)."""

import struct

HEADER = struct.Struct('<4s3I3I3I4I6B2x2i3I2i3IQII128s8x')
CELL = struct.Struct('<6IHBB')


class Writer:
    def __init__(self, path, max_cols=64, max_rows=32, combining_capacity=256):
        self.path = path
        self.max_cols, self.max_rows, self.cap = max_cols, max_rows, combining_capacity
        self.cells_offset = 256
        self.dirty_offset = self.cells_offset + max_cols * max_rows * CELL.size
        self.combining_offset = self.dirty_offset + max_rows
        self.size = self.combining_offset + 4 * combining_capacity
        with open(path, 'wb') as f:
            f.write(bytes(self.size))
        self.seq, self.frames = 0, 0
        self.fields = dict(
            magic=b'KEPM', version=1, header_size=256, cell_size=28, max_cols=max_cols, max_rows=max_rows,
            combining_capacity=combining_capacity, cells_offset=self.cells_offset, dirty_offset=self.dirty_offset,
            combining_offset=self.combining_offset, seq=0, cols=0, rows=0, modes=0, mouse_mode=0, mouse_proto=0,
            cursor_shape=0, cursor_visible=1, cursor_blink=1, cursor_focused=0, cursor_x=0, cursor_y=0,
            cursor_color=0x01000000, default_fg=0xdddddd, default_bg=0, history_rows=0, view_offset=0,
            combining_len=1, key_flags=0, reserved1=0, frames=0, child_pid=0, title_len=0, title=b'')
        # As the engine does on attach: a header, no frame yet.
        with open(path, 'r+b') as f:
            f.write(self._pack())

    def _pack(self):
        f = self.fields
        return HEADER.pack(*f.values())

    def write(self, cols, rows, cells, combining=(), dirty=None, odd=False, **header):
        """cells: list of tuples (ch, extra, fg, bg, ul, link, attrs, underline, reserved)."""
        assert len(cells) == cols * rows
        title = header.pop('title', None)
        self.fields.update(header)
        if title is not None:
            data = title.encode('utf-8')[:128]
            self.fields['title'], self.fields['title_len'] = data, len(data)
        with open(self.path, 'r+b') as f:
            self.fields['seq'] = self.seq + 1
            f.seek(0); f.write(self._pack()); f.flush()
            self.fields['cols'], self.fields['rows'] = cols, rows
            f.seek(self.cells_offset)
            f.write(b''.join(CELL.pack(*c) for c in cells))
            f.seek(self.dirty_offset)
            f.write(bytes(dirty if dirty is not None else [1] * rows))
            comb = [0, *combining]
            f.seek(self.combining_offset)
            f.write(struct.pack(f'<{len(comb)}I', *comb))
            self.fields['combining_len'] = len(comb)
            self.frames += 1
            self.fields['frames'] = self.frames
            self.seq += 1 if odd else 2
            self.fields['seq'] = self.seq
            f.seek(0); f.write(self._pack())


def cell(ch=' ', fg=0xdddddd, bg=0, attrs=0, underline=0, extra=0):
    return (ord(ch) if isinstance(ch, str) else ch, extra, fg, bg, 0x01000000, 0, attrs, underline, 0)
