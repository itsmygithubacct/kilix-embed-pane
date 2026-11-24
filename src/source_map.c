/* Reading a KEPM map: see kep_map.h for the layout and the seqlock rule. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "kep_map.h"
#include "kep_source.h"

#define READ_TRIES 1000

struct kep_map {
    const uint8_t *base;
    size_t size;
    kep_map_header h;       /* last consistent header */
    uint64_t last_frames;
    int have_frame;
    /* What kep_map_read handed out, reused across reads. */
    kep_cell *cells;
    size_t cells_cap;
    uint8_t *dirty;
    size_t dirty_cap;
    uint32_t *combining;
    size_t combining_cap;
};

static uint32_t load_seq(const kep_map *m) {
    return __atomic_load_n((const uint32_t *)(m->base + offsetof(kep_map_header, seq)), __ATOMIC_ACQUIRE);
}

kep_map *kep_map_open(const char *path, int *err) {
    int code = KEP_ERR_IO;
    kep_map *m = NULL;
    if (!path) { errno = EINVAL; code = KEP_ERR_ARG; goto fail; }
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) goto fail;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { if (!errno) errno = EINVAL; close(fd); goto fail; }
    if ((size_t)st.st_size < sizeof(kep_map_header)) { close(fd); errno = EINVAL; code = KEP_ERR_FORMAT; goto fail; }
    void *base = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (base == MAP_FAILED) goto fail;
    m = calloc(1, sizeof *m);
    if (!m) { munmap(base, (size_t)st.st_size); code = KEP_ERR_NOMEM; errno = ENOMEM; goto fail; }
    m->base = base; m->size = (size_t)st.st_size;
    kep_map_header h;
    memcpy(&h, m->base, sizeof h);
    uint64_t cells_end = (uint64_t)h.cells_offset + (uint64_t)h.max_cols * h.max_rows * sizeof(kep_cell);
    uint64_t comb_end = (uint64_t)h.combining_offset + 4ull * h.combining_capacity;
    if (memcmp(h.magic, KEP_MAP_MAGIC, 4) || h.version != KEP_MAP_VERSION || h.header_size != KEP_MAP_HEADER_SIZE
            || h.cell_size != sizeof(kep_cell) || h.cells_offset < KEP_MAP_HEADER_SIZE || h.max_cols == 0
            || h.max_rows == 0 || h.dirty_offset < cells_end || h.combining_offset < (uint64_t)h.dirty_offset + h.max_rows
            || comb_end > m->size || (h.cells_offset % 4) || (h.combining_offset % 4)) {
        code = KEP_ERR_FORMAT; errno = EINVAL;
        kep_map_close(m); m = NULL;
        goto fail;
    }
    m->h = h;
    if (err) *err = KEP_OK;
    return m;
fail:
    if (err) *err = code;
    return NULL;
}

void kep_map_close(kep_map *m) {
    if (!m) return;
    if (m->base) munmap((void *)m->base, m->size);
    free(m->cells); free(m->dirty); free(m->combining);
    free(m);
}

static int reserve(void **p, size_t *cap, size_t need, size_t elem) {
    if (need <= *cap) return 0;
    void *q = realloc(*p, need * elem);
    if (!q) return -1;
    *p = q; *cap = need;
    return 0;
}

int kep_map_read(kep_map *m, kep_grid *grid, int force) {
    if (!m || !grid) return KEP_ERR_ARG;
    const kep_map_header fixed = m->h;   /* offsets and capacities never change */
    for (int attempt = 0; attempt < READ_TRIES; attempt++) {
        uint32_t seq = load_seq(m);
        if (seq & 1) continue;
        kep_map_header h;
        memcpy(&h, m->base, sizeof h);
        if (h.frames == 0) {   /* attached, no frame written yet */
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
            if (load_seq(m) == seq) return KEP_UNCHANGED;
            continue;
        }
        if (!force && m->have_frame && h.frames == m->last_frames) {
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
            if (load_seq(m) == seq) return KEP_UNCHANGED;
            continue;
        }
        size_t n = (size_t)h.cols * h.rows;
        if (h.cols == 0 || h.rows == 0 || h.cols > fixed.max_cols || h.rows > fixed.max_rows
                || h.combining_len > fixed.combining_capacity) {
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
            if (load_seq(m) == seq) return KEP_ERR_FORMAT;
            continue;
        }
        if (reserve((void **)&m->cells, &m->cells_cap, n, sizeof(kep_cell))
                || reserve((void **)&m->dirty, &m->dirty_cap, h.rows, 1)
                || reserve((void **)&m->combining, &m->combining_cap, h.combining_len ? h.combining_len : 1, 4))
            return KEP_ERR_NOMEM;
        memcpy(m->cells, m->base + fixed.cells_offset, n * sizeof(kep_cell));
        memcpy(m->dirty, m->base + fixed.dirty_offset, h.rows);
        if (h.combining_len) memcpy(m->combining, m->base + fixed.combining_offset, 4ull * h.combining_len);
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (load_seq(m) != seq) continue;

        int missed = force || !m->have_frame || h.frames != m->last_frames + 1;
        m->have_frame = 1;
        m->last_frames = h.frames;
        if (missed) memset(m->dirty, 1, h.rows);
        m->h = h;
        /* Keep the fixed layout fields from open, whatever this header said. */
        m->h.cells_offset = fixed.cells_offset; m->h.dirty_offset = fixed.dirty_offset;
        m->h.combining_offset = fixed.combining_offset; m->h.max_cols = fixed.max_cols;
        m->h.max_rows = fixed.max_rows; m->h.combining_capacity = fixed.combining_capacity;

        /* Combining offsets the consumer follows must stay inside the copy. */
        for (size_t i = 0; i < n; i++) {
            uint32_t e = m->cells[i].extra;
            if (e && (e >= h.combining_len || e + 1ull + m->combining[e] > h.combining_len)) m->cells[i].extra = 0;
        }
        grid->cols = (int)h.cols; grid->rows = (int)h.rows;
        grid->cells = m->cells;
        grid->combining = m->combining;
        grid->combining_len = h.combining_len;
        grid->cursor.x = h.cursor_x; grid->cursor.y = h.cursor_y;
        grid->cursor.shape = h.cursor_shape; grid->cursor.visible = h.cursor_visible;
        grid->cursor.blink = h.cursor_blink; grid->cursor.focused = h.cursor_focused;
        grid->cursor.color = h.cursor_color;
        grid->default_fg = h.default_fg; grid->default_bg = h.default_bg;
        grid->dirty_rows = m->dirty;
        grid->history_rows = h.history_rows; grid->view_offset = h.view_offset;
        return KEP_OK;
    }
    return KEP_ERR_BUSY;
}

void kep_map_info_get(const kep_map *m, kep_map_info *out) {
    if (!m || !out) return;
    memset(out, 0, sizeof *out);
    out->modes = m->h.modes;
    out->mouse_tracking_mode = m->h.mouse_tracking_mode;
    out->mouse_tracking_protocol = m->h.mouse_tracking_protocol;
    out->key_encoding_flags = m->h.key_encoding_flags;
    out->frames = m->h.frames;
    out->child_pid = m->h.child_pid;
    size_t tl = m->h.title_len < sizeof m->h.title ? m->h.title_len : sizeof m->h.title;
    memcpy(out->title, m->h.title, tl);
    out->title[tl] = 0;
}

void kep_grid_free(kep_grid *grid) {
    /* The arrays belong to the kep_map; a grid only points at them. */
    if (grid) memset(grid, 0, sizeof *grid);
}
