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
