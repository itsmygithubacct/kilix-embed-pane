# kilix-embed-pane

Kilix panes drawn inside an application's own pixels: a game room, a desktop,
a dashboard. The pane stays a Kilix pane (listed, addressable, resizable,
with scrollback); the application only decides where it goes and how it looks.

Layers:

- **Source**: where a pane's output comes from and its input goes: an embedded
  Kilix window, a read-only view of a desktop pane through its PTY broker, or a
  plain PTY.
- **Screen**: the terminal state as cells (`include/kep_cells.h`), with
  scrollback, selection and mouse modes.
- **Draw**: cells into a caller-owned pixel buffer through a renderer table
  (Neuronte nets, or the system font).

C11 core with no Python dependency; a thin Python binding for Zell.
Work in progress: nothing here is released.

## Embedded Kilix windows (the source layer)

A Kilix engine with embedded windows (`launch --type=embed`) runs the pane
and writes its screen to a cell map (`include/kep_map.h`). The program
drawing it reads the map and sends input back with `embed-input`:

```python
import kilix_embed_pane as kep

engine = kep.Engine.from_environment()        # KITTY_LISTEN_ON, the engine's kitten
if engine.supported():
    window = engine.launch(['bash'], 80, 24, cell_size=(8, 16))
    reader = kep.MapReader(window.map)
    conn = kep.InputConnection(engine.listen_on)
    conn.send(window, ['text', 'ls\r'])
    frame = reader.read()                       # None until something changed
    records = kep.to_records(frame)            # for neuronte-display's Renderer
    engine.resize(window, 100, 30, (8, 16))
    conn.send(window, ['mouse', '0', str(kep.BUTTON_LEFT), '0', '0'])
    text = conn.selection(window)               # after the events sent before it
    engine.close(window)
```

C: `kep_map_open`/`kep_map_read` fill a `kep_grid`; `kep_rc_open` and
`kep_rc_text`/`_bytes`/`_mouse`/`_scroll`/`_focus` send input
(`include/kep_source.h`). The window keeps everything else of a Kilix pane:
`ls`, `get-text`, `send-text` and `launch --env` work on it as on any other.

Tests: `make test` (C and Python), and `ENGINE=<built kitty checkout>
tests/engine/run.sh` against a private engine under Xvfb (add
`KILIX=<Kilix checkout>` to use Kilix's remote-control config).
