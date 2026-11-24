/* Source layer tests: a map written the way the engine writes it, read back;
 * embed-input encoding; one connection for many events. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "kep_map.h"
#include "kep_source.h"

static int failures, checks;
#define CHECK(c) do { checks++; if (!(c)) { failures++; fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); } } while (0)

enum { MC = 16, MR = 8, CAP = 64 };

typedef struct { uint8_t *base; size_t size; kep_map_header *h; char path[600]; } writer;

static void w_open(writer *w, const char *dir) {
    snprintf(w->path, sizeof w->path, "%s/w.kepm", dir);
    w->size = 256 + MC * MR * sizeof(kep_cell) + MR + 4 * CAP;
    int fd = open(w->path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0 || ftruncate(fd, (off_t)w->size)) { perror("map"); exit(2); }
    w->base = mmap(NULL, w->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    w->h = (kep_map_header *)w->base;
    memcpy(w->h->magic, KEP_MAP_MAGIC, 4);
    w->h->version = KEP_MAP_VERSION; w->h->header_size = 256; w->h->cell_size = sizeof(kep_cell);
    w->h->max_cols = MC; w->h->max_rows = MR; w->h->combining_capacity = CAP;
    w->h->cells_offset = 256;
    w->h->dirty_offset = 256 + MC * MR * sizeof(kep_cell);
    w->h->combining_offset = w->h->dirty_offset + MR;
    w->h->combining_len = 1;
}

static kep_cell *w_cells(writer *w) { return (kep_cell *)(w->base + w->h->cells_offset); }
static uint8_t *w_dirty(writer *w) { return w->base + w->h->dirty_offset; }
static uint32_t *w_comb(writer *w) { return (uint32_t *)(w->base + w->h->combining_offset); }
static void w_begin(writer *w) { __atomic_store_n(&w->h->seq, w->h->seq + 1, __ATOMIC_RELEASE); }
static void w_end(writer *w) { w->h->frames++; __atomic_store_n(&w->h->seq, w->h->seq + 1, __ATOMIC_RELEASE); }

static void w_text(writer *w, int cols, int rows, const char *text) {
    w_begin(w);
    w->h->cols = (uint32_t)cols; w->h->rows = (uint32_t)rows;
    kep_cell *c = w_cells(w);
    memset(c, 0, (size_t)cols * rows * sizeof *c);
    for (int i = 0; i < cols * rows && text[i]; i++) { c[i].ch = (uint8_t)text[i]; c[i].fg = 0xdddddd; }
    memset(w_dirty(w), 1, (size_t)rows);
    w_end(w);
}

static void test_map(const char *dir) {
    writer w; w_open(&w, dir);
    int err = 0;
    kep_map *m = kep_map_open(w.path, &err);
    CHECK(m && err == KEP_OK);
    kep_grid g = {0};
    /* No frame yet. */
    CHECK(kep_map_read(m, &g, 0) == KEP_UNCHANGED && kep_map_read(m, &g, 1) == KEP_UNCHANGED);

    w_text(&w, 4, 2, "abcdefgh");
    CHECK(kep_map_read(m, &g, 0) == KEP_OK);
    CHECK(g.cols == 4 && g.rows == 2 && g.cells[5].ch == 'f' && g.dirty_rows[0] && g.dirty_rows[1]);
    CHECK(kep_map_read(m, &g, 0) == KEP_UNCHANGED);
    CHECK(kep_map_read(m, &g, 1) == KEP_OK);

    /* Consecutive frame: dirty bytes as written. */
    w_begin(&w); w_cells(&w)[0].ch = 'Z'; w_dirty(&w)[0] = 1; w_dirty(&w)[1] = 0; w_end(&w);
    CHECK(kep_map_read(m, &g, 0) == KEP_OK && g.cells[0].ch == 'Z' && g.dirty_rows[0] && !g.dirty_rows[1]);

    /* Two frames, one read: every row dirty. */
    w_begin(&w); w_dirty(&w)[0] = w_dirty(&w)[1] = 0; w_end(&w);
    w_begin(&w); w_dirty(&w)[0] = w_dirty(&w)[1] = 0; w_end(&w);
    CHECK(kep_map_read(m, &g, 0) == KEP_OK && g.dirty_rows[0] && g.dirty_rows[1]);

    /* Mid-write: never handed out. */
    w_begin(&w);
    CHECK(kep_map_read(m, &g, 1) == KEP_ERR_BUSY);
    w_end(&w);

    /* Header fields, combining marks, a bad combining offset dropped. */
    w_begin(&w);
    w.h->cursor_x = 3; w.h->cursor_y = 1; w.h->cursor_shape = KEP_CURSOR_BEAM; w.h->cursor_visible = 1;
    w.h->modes = KEP_MODE_ALTERNATE_SCREEN; w.h->history_rows = 40; w.h->view_offset = 2;
    w.h->child_pid = 1234; memcpy(w.h->title, "vim", 3); w.h->title_len = 3;
    uint32_t *cb = w_comb(&w); cb[0] = 0; cb[1] = 1; cb[2] = 0x301; w.h->combining_len = 3;
    w_cells(&w)[1].extra = 1; w_cells(&w)[2].extra = 2; w_cells(&w)[3].extra = 60;
    w_end(&w);
    CHECK(kep_map_read(m, &g, 0) == KEP_OK);
    CHECK(g.cursor.x == 3 && g.cursor.y == 1 && g.cursor.shape == KEP_CURSOR_BEAM && g.cursor.visible);
    CHECK(g.history_rows == 40 && g.view_offset == 2 && g.combining_len == 3);
    CHECK(g.cells[1].extra == 1 && g.combining[g.cells[1].extra] == 1 && g.combining[2] == 0x301);
    CHECK(g.cells[2].extra == 0);   /* count 0x301 runs past the end */
    CHECK(g.cells[3].extra == 0);   /* past combining_len */
    kep_map_info info; kep_map_info_get(m, &info);
    CHECK(info.modes == KEP_MODE_ALTERNATE_SCREEN && info.child_pid == 1234 && !strcmp(info.title, "vim"));

    /* Size beyond the map's maximum is refused, not read. */
    w_begin(&w); w.h->cols = MC + 1; w_end(&w);
    CHECK(kep_map_read(m, &g, 0) == KEP_ERR_FORMAT);
    /* A header that moves its own offsets is ignored: the layout from open stays. */
    w_begin(&w); w.h->cols = 4; w.h->cells_offset = 0xfffffff0u; w_end(&w);
    CHECK(kep_map_read(m, &g, 0) == KEP_OK && g.cells[0].ch == 'Z');
    w.h->cells_offset = 256;

    kep_grid_free(&g);
    CHECK(g.cells == NULL);
    kep_map_close(m);

    /* Foreign and truncated files. */
    memcpy(w.h->magic, "NOPE", 4);
    CHECK(!kep_map_open(w.path, &err) && err == KEP_ERR_FORMAT);
    memcpy(w.h->magic, KEP_MAP_MAGIC, 4);
    w.h->combining_capacity = 1u << 30;
    CHECK(!kep_map_open(w.path, &err) && err == KEP_ERR_FORMAT);
    w.h->combining_capacity = CAP;
    char link[600]; snprintf(link, sizeof link, "%s/link", dir);
    CHECK(!symlink(w.path, link));
    CHECK(!kep_map_open(link, &err) && err == KEP_ERR_IO);
    unlink(link);
    munmap(w.base, w.size);
    unlink(w.path);
}

static void test_encode(void) {
    const char *ev[] = {"text", "a\"b\\c\r\x01\xff\xc3\xa9"};
    size_t len = 0;
    char *s = kep_rc_encode(7, "tok", 3, ev, 2, &len);
    CHECK(s && len == strlen(s));
    const char *want = "\x1bP@kitty-cmd{\"cmd\":\"embed-input\",\"version\":[0,14,0],\"no_response\":true,"
                       "\"payload\":{\"match\":\"id:7\",\"token\":\"tok\",\"mods\":3,\"event\":[\"text\","
                       "\"a\\\"b\\\\c\\u000d\\u0001\xef\xbf\xbd\xc3\xa9\"]}}\x1b\\";
    CHECK(s && !strcmp(s, want));
    if (s && strcmp(s, want)) fprintf(stderr, "got: %s\n", s);
    free(s);
    CHECK(!kep_rc_encode(1, "t", 256, ev, 2, NULL));
    CHECK(!kep_rc_encode(1, "t", 0, ev, 0, NULL));
    CHECK(!kep_rc_encode(1, NULL, 0, ev, 2, NULL));
    /* Overlong and surrogate encodings are replaced. */
    const char *bad[] = {"text", "\xc0\xaf\xed\xa0\x80"};
    s = kep_rc_encode(1, "t", 0, bad, 2, NULL);
    CHECK(s && strstr(s, "\"\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd\xef\xbf\xbd\""));
    free(s);
}

typedef struct { int listen_fd; int accepted; char data[8192]; size_t len; } server;

static void *serve(void *arg) {
    server *s = arg;
    for (;;) {
        int c = accept(s->listen_fd, NULL, NULL);
        if (c < 0) return NULL;
        s->accepted++;
        ssize_t n;
        while ((n = read(c, s->data + s->len, sizeof s->data - 1 - s->len)) > 0) s->len += (size_t)n;
        close(c);
    }
}

static int count(const char *hay, const char *needle) {
    int n = 0;
    for (const char *p = hay; (p = strstr(p, needle)); p++) n++;
    return n;
}

static void test_connection(void) {
    char name[64]; snprintf(name, sizeof name, "@kep-source-test-%d", (int)getpid());
    server s = {0};
    s.listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    memcpy(a.sun_path + 1, name + 1, strlen(name) - 1);
    socklen_t al = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + strlen(name));
    CHECK(!bind(s.listen_fd, (struct sockaddr *)&a, al) && !listen(s.listen_fd, 4));
    pthread_t t; pthread_create(&t, NULL, serve, &s);

    char lo[80]; snprintf(lo, sizeof lo, "unix:%s", name);
    int err;
    kep_rc *rc = kep_rc_open(lo, &err);
    CHECK(rc && err == KEP_OK);
    CHECK(kep_rc_text(rc, 5, "tk", "ls\r") == KEP_OK);
    CHECK(kep_rc_bytes(rc, 5, "tk", "\x1b[A\xff", 4) == KEP_OK);
    CHECK(kep_rc_mouse(rc, 5, "tk", 1, 0, 1, 3, 4, 24, 64, 1) == KEP_OK);
    CHECK(kep_rc_scroll(rc, 5, "tk", 0, 3, 1, 1) == KEP_OK);
    CHECK(kep_rc_focus(rc, 5, "tk", 0) == KEP_OK);
    kep_rc_close(rc);
    for (int i = 0; i < 200 && count(s.data, "\x1b\\") < 5; i++) usleep(10000);
    shutdown(s.listen_fd, SHUT_RDWR); close(s.listen_fd);
    pthread_join(t, NULL);
    s.data[s.len] = 0;
    CHECK(s.accepted == 1);
    CHECK(count(s.data, "\x1bP@kitty-cmd") == 5);
    CHECK(strstr(s.data, "\"event\":[\"bytes\",\"G1tB/w==\"]"));
    CHECK(strstr(s.data, "\"mods\":1,\"event\":[\"mouse\",\"0\",\"1\",\"3\",\"4\",\"24\",\"64\",\"1\"]"));
    CHECK(strstr(s.data, "\"event\":[\"scroll\",\"3\",\"1\",\"1\"]"));
    CHECK(strstr(s.data, "\"event\":[\"focus\",\"out\"]"));

    /* Nobody listening: an error, not a crash or a hang. */
    rc = kep_rc_open("unix:@kep-source-nobody-xyz", &err);
    CHECK(rc && kep_rc_text(rc, 1, "t", "x") == KEP_ERR_IO);
    kep_rc_close(rc);
    CHECK(!kep_rc_open("tcp:1.2.3.4:5", &err) && err == KEP_ERR_ARG);
    CHECK(!kep_rc_open("unix:", &err) && err == KEP_ERR_ARG);
}

int main(void) {
    char dir[] = "/tmp/kep-source-XXXXXX";
    const char *tmp = getenv("TMPDIR");
    char buf[512];
    if (tmp && *tmp) { snprintf(buf, sizeof buf, "%s/kep-source-XXXXXX", tmp); }
    else snprintf(buf, sizeof buf, "%s", dir);
    if (!mkdtemp(buf)) { perror("mkdtemp"); return 2; }
    test_map(buf);
    test_encode();
    test_connection();
    rmdir(buf);
    printf("%d/%d checks passed\n", checks - failures, checks);
    return failures != 0;
}
