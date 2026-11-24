/* kep_map.h - the cell map a Kilix engine writes for an embedded window.
 *
 * `kitten @ launch --type=embed` answers with JSON naming a file (`map`); the
 * engine (kitty fork, kitty/embed-export.c) sizes it once and rewrites it
 * every render tick that the window changed. Map it read-only and copy a
 * frame out under the sequence lock:
 *
 *     do { s = seq (acquire); if (s & 1) retry; copy; } while (seq != s);
 *
 * dirty[] marks rows changed since the previous frame. A reader that missed
 * frames (frames advanced by more than one since it last copied) must treat
 * every row as changed.
 */
#ifndef KEP_MAP_H
#define KEP_MAP_H

#include "kep_cells.h"

#include <stdint.h>

#define KEP_MAP_MAGIC "KEPM"
#define KEP_MAP_VERSION 1u
#define KEP_MAP_HEADER_SIZE 256u

/* kep_map_header.modes */
enum {
    KEP_MODE_BRACKETED_PASTE    = 1u << 0,
    KEP_MODE_FOCUS_TRACKING     = 1u << 1,
    KEP_MODE_ALTERNATE_SCREEN   = 1u << 2,
    KEP_MODE_APPLICATION_CURSOR = 1u << 3,
    KEP_MODE_REVERSE_VIDEO      = 1u << 4,
};

/* kep_map_header.mouse_tracking_mode: 0 none, 1 buttons, 2 drags, 3 all motion.
 * The engine encodes mouse events itself (embed-input mouse); these tell the
 * embedding program whether a click belongs to the program or to selection. */

typedef struct kep_map_header {
    char magic[4];                  /* "KEPM" */
    uint32_t version;               /* KEP_MAP_VERSION */
    uint32_t header_size;           /* 256 */
    uint32_t cell_size;             /* sizeof(kep_cell) == 28 */
    uint32_t max_cols, max_rows, combining_capacity;
    uint32_t cells_offset;          /* kep_cell[rows][cols], packed at the current cols */
    uint32_t dirty_offset;          /* uint8_t[rows] */
    uint32_t combining_offset;      /* uint32_t[combining_len], see kep_grid.combining */
    uint32_t seq;                   /* odd while a frame is being written */
    uint32_t cols, rows;
    uint32_t modes;                 /* KEP_MODE_* */
    uint8_t mouse_tracking_mode;
    uint8_t mouse_tracking_protocol;/* 0 normal, 1 utf8, 2 sgr, 3 urxvt, 4 sgr-pixels */
    uint8_t cursor_shape;           /* KEP_CURSOR_* */
    uint8_t cursor_visible, cursor_blink, cursor_focused;
    uint8_t reserved0[2];
    int32_t cursor_x, cursor_y;
    uint32_t cursor_color;          /* 0x00RRGGBB or KEP_COLOR_SAME_AS_FG */
    uint32_t default_fg, default_bg;
    int32_t history_rows, view_offset;
    uint32_t combining_len;
    uint32_t key_encoding_flags;    /* kitty keyboard protocol flags in force */
    uint32_t reserved1;
    uint64_t frames;                /* frames written so far */
    uint32_t child_pid;
    uint32_t title_len;             /* bytes of title used */
    char title[128];                /* the window's title, UTF-8, never a split character */
    uint8_t reserved[8];
} kep_map_header;

_Static_assert(sizeof(kep_map_header) == KEP_MAP_HEADER_SIZE, "kep_map_header is 256 bytes");
_Static_assert(sizeof(kep_cell) == 28, "kep_cell is 28 bytes in the map");

#endif
