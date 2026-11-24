/* kep_cells.h - a terminal screen as cells: what the Screen layer (or the
 * Kilix engine, for an embedded window) hands to the Draw layer.
 *
 * Shared contract. The Source/Screen side fills a kep_grid; the Draw side
 * reads it and never sees bytes, escape sequences or the parser. Colours are
 * already resolved (palette, reverse video, default colours, selection), so a
 * renderer only paints what it is given.
 *
 * Pixels everywhere in this module are native uint32_t 0xAARRGGBB, rows
 * `stride` pixels apart, matching neuronte-sdk's canvas and PuzzLLM's frames.
 */
#ifndef KEP_CELLS_H
#define KEP_CELLS_H

#include <stddef.h>
#include <stdint.h>

#define KEP_CELLS_ABI 1

/* kep_cell.attrs */
enum {
    KEP_ATTR_BOLD      = 1u << 0,
    KEP_ATTR_ITALIC    = 1u << 1,
    KEP_ATTR_DIM       = 1u << 2,
    KEP_ATTR_STRIKE    = 1u << 3,
    KEP_ATTR_BLINK     = 1u << 4,
    KEP_ATTR_INVISIBLE = 1u << 5,
    KEP_ATTR_WIDE      = 1u << 6,  /* first half of a two-cell character */
    KEP_ATTR_WIDE_TAIL = 1u << 7,  /* second half: draw nothing, bg only */
    KEP_ATTR_SELECTED  = 1u << 8,  /* informational; colours already applied */
    KEP_ATTR_OVERLINE  = 1u << 9,
};

/* kep_cell.underline */
enum {
    KEP_UL_NONE = 0,
    KEP_UL_SINGLE,
    KEP_UL_DOUBLE,
    KEP_UL_CURLY,
    KEP_UL_DOTTED,
    KEP_UL_DASHED,
};

/* kep_cell.ul_color when the underline takes the text colour. */
#define KEP_COLOR_SAME_AS_FG 0x01000000u

typedef struct kep_cell {
    uint32_t ch;        /* first Unicode scalar; 0 is an empty cell */
    uint32_t extra;     /* 0, or an offset into kep_grid.combining (see there) */
    uint32_t fg;        /* 0x00RRGGBB, final */
    uint32_t bg;        /* 0x00RRGGBB, final */
    uint32_t ul_color;  /* 0x00RRGGBB or KEP_COLOR_SAME_AS_FG */
    uint32_t link;      /* hyperlink id (OSC 8), 0 for none */
    uint16_t attrs;     /* KEP_ATTR_* */
    uint8_t underline;  /* KEP_UL_* */
    uint8_t reserved;   /* zero */
} kep_cell;

/* kep_cursor.shape */
enum { KEP_CURSOR_BLOCK = 0, KEP_CURSOR_BEAM, KEP_CURSOR_UNDERLINE, KEP_CURSOR_HOLLOW };

typedef struct kep_cursor {
    int x, y;           /* cell; may be outside the grid when hidden */
    uint8_t shape;      /* KEP_CURSOR_* */
    uint8_t visible;
    uint8_t blink;
    uint8_t focused;    /* 0 draws the unfocused (hollow) form */
    uint32_t color;     /* 0x00RRGGBB, or KEP_COLOR_SAME_AS_FG for the cell's fg */
} kep_cursor;

typedef struct kep_grid {
    int cols, rows;
    const kep_cell *cells;      /* rows * cols, row-major */
    /* Combining marks: cell.extra = i > 0 means combining[i] is a count n,
     * then n scalars follow. combining[0] is unused so 0 means none. */
    const uint32_t *combining;
    size_t combining_len;
    kep_cursor cursor;
    uint32_t default_fg, default_bg;
    /* rows bytes; non-zero = the row changed since the last kep_grid the
     * consumer saw. NULL means every row changed. */
    const uint8_t *dirty_rows;
    /* Scrollback position, for scrollbars: rows kept above the screen, and
     * how far up the view is (0 = at the bottom, following output). */
    int history_rows;
    int view_offset;
} kep_grid;

#endif
