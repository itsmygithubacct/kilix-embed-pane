"""kilix-embed-pane, Python side: Kilix windows drawn by another program.

A program asks the Kilix engine for an embedded window (``launch
--type=embed``), maps the window's cell file read-only (MapReader), sends it
input over one long-lived socket connection (InputConnection), and draws its
cells however it likes. Standard library only.

    engine = Engine.from_environment()
    window = engine.launch(['bash'], cols=80, rows=24, cell_size=(8, 16))
    reader = MapReader(window.map)
    frame = reader.read()            # None until something changed
    conn = InputConnection(engine.listen_on)
    conn.send(window, ['text', 'ls\\r'])
"""

from .kepmap import (
    ATTR_BLINK, ATTR_BOLD, ATTR_DIM, ATTR_INVISIBLE, ATTR_ITALIC, ATTR_OVERLINE, ATTR_SELECTED, ATTR_STRIKE,
    ATTR_WIDE, ATTR_WIDE_TAIL, COLOR_SAME_AS_FG, MODE_ALTERNATE_SCREEN, MODE_APPLICATION_CURSOR,
    MODE_BRACKETED_PASTE, MODE_FOCUS_TRACKING, MODE_REVERSE_VIDEO, Cell, Frame, MapError, MapReader,
)
from .control import EmbedError, EmbeddedWindow, Engine, InputConnection, encode_command
from .records import cell_text, to_records

__all__ = [
    'ATTR_BLINK', 'ATTR_BOLD', 'ATTR_DIM', 'ATTR_INVISIBLE', 'ATTR_ITALIC', 'ATTR_OVERLINE', 'ATTR_SELECTED',
    'ATTR_STRIKE', 'ATTR_WIDE', 'ATTR_WIDE_TAIL', 'COLOR_SAME_AS_FG', 'MODE_ALTERNATE_SCREEN',
    'MODE_APPLICATION_CURSOR', 'MODE_BRACKETED_PASTE', 'MODE_FOCUS_TRACKING', 'MODE_REVERSE_VIDEO',
    'Cell', 'EmbedError', 'EmbeddedWindow', 'Engine', 'Frame', 'InputConnection', 'MapError', 'MapReader',
    'cell_text', 'encode_command', 'to_records',
]
