/* kep_source.h: where an embedded Kilix window's cells come from, and where
 * its input goes.
 *
 * kep_map_* maps a window's KEPM file (kep_map.h) read-only and copies a
 * consistent frame into a kep_grid (kep_cells.h). kep_rc_* keeps one socket
 * connection to the engine and sends embed-input commands carrying the
 * window's token. Launching and resizing need the caller's remote-control
 * credential and are left to `kitten @` (see python/kilix_embed_pane).
 *
 * Nothing here picks a socket or runtime directory for the caller: the map
 * path and the socket address are always given explicitly.
 */
#ifndef KEP_SOURCE_H
#define KEP_SOURCE_H

#include <stddef.h>
#include <stdint.h>

#include "kep_cells.h"

#ifdef __cplusplus
extern "C" {
#endif

enum {
    KEP_OK = 0,
    KEP_UNCHANGED = 1,      /* kep_map_read: nothing new since the last read */
    KEP_ERR_IO = -1,        /* see errno */
    KEP_ERR_FORMAT = -2,    /* not a version 1 KEPM map, or out of range */
    KEP_ERR_BUSY = -3,      /* the map kept changing while being read */
    KEP_ERR_ARG = -4,
    KEP_ERR_NOMEM = -5,
};

typedef struct kep_map kep_map;

/* Opens and maps `path` read-only (no symlinks). NULL on failure, errno set;
 * *err, when given, gets a KEP_ERR_ code. */
kep_map *kep_map_open(const char *path, int *err);
void kep_map_close(kep_map *m);

/* Copies the latest frame into `grid`. The grid's arrays belong to the map
 * and stay valid until the next kep_map_read or kep_map_close; the window
 * writing the map never changes them. KEP_UNCHANGED when no frame was
 * written since the last read, unless `force`. When frames were missed,
 * every row is marked dirty. */
int kep_map_read(kep_map *m, kep_grid *grid, int force);

/* Header extras not in kep_grid. Valid after a successful read. */
typedef struct kep_map_info {
    uint32_t modes;                 /* KEP_MODE_ bits, kep_map.h */
    uint8_t mouse_tracking_mode, mouse_tracking_protocol;
    uint32_t key_encoding_flags;
    uint64_t frames;
    uint32_t child_pid;
    char title[129];                /* NUL-terminated UTF-8 */
} kep_map_info;
void kep_map_info_get(const kep_map *m, kep_map_info *out);

/* Clears a grid (it owns nothing). */
void kep_grid_free(kep_grid *grid);

/* The engine's socket: "unix:/path" or "unix:@abstract". */
typedef struct kep_rc kep_rc;
kep_rc *kep_rc_open(const char *listen_on, int *err);
void kep_rc_close(kep_rc *rc);

/* One embed-input event: kind ("text", "bytes", "paste", "key", "mouse",
 * "scroll", "focus") and its string arguments. Reconnects once if the
 * connection broke. */
int kep_rc_input(kep_rc *rc, unsigned window_id, const char *token, unsigned mods,
                 const char *const *event, size_t nargs);

/* Conveniences over kep_rc_input. */
int kep_rc_text(kep_rc *rc, unsigned window_id, const char *token, const char *utf8);
int kep_rc_bytes(kep_rc *rc, unsigned window_id, const char *token, const void *data, size_t len);
/* action 0 press, 1 release, 2 move; cell and pixel-in-pane coordinates. */
int kep_rc_mouse(kep_rc *rc, unsigned window_id, const char *token, unsigned mods, int action, int button,
                 int cell_x, int cell_y, int px, int py, int left_half);
/* lines > 0 scrolls back (wheel up). */
int kep_rc_scroll(kep_rc *rc, unsigned window_id, const char *token, unsigned mods, int lines,
                  int cell_x, int cell_y);
int kep_rc_focus(kep_rc *rc, unsigned window_id, const char *token, int focused);

/* The bytes kep_rc_input would send, for tests: NUL-terminated, malloc'd. */
char *kep_rc_encode(unsigned window_id, const char *token, unsigned mods, const char *const *event,
                    size_t nargs, size_t *len);

#ifdef __cplusplus
}
#endif
#endif
