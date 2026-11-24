/* embed-input over one connection to the engine's remote-control socket.
 *
 * Frame: ESC P @kitty-cmd {json} ESC \  with "no_response": true, so the
 * engine sends nothing back and the connection stays open for the next one.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "kep_source.h"

#define MAX_ARGS 66

struct kep_rc {
    struct sockaddr_un addr;
    socklen_t addr_len;
    int fd;
};

typedef struct { char *p; size_t len, cap; int bad; } buf;

static void put(buf *b, const char *s, size_t n) {
    if (b->bad) return;
    if (b->len + n + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 256;
        while (b->len + n + 1 > cap) cap *= 2;
        char *q = realloc(b->p, cap);
        if (!q) { b->bad = 1; return; }
        b->p = q; b->cap = cap;
    }
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = 0;
}
static void puts_(buf *b, const char *s) { put(b, s, strlen(s)); }

/* A JSON string; invalid UTF-8 bytes become U+FFFD. */
static void put_json_string(buf *b, const char *s) {
    put(b, "\"", 1);
    const unsigned char *u = (const unsigned char *)s;
    char tmp[8];
    while (*u) {
        unsigned c = *u;
        if (c == '"' || c == '\\') { put(b, "\\", 1); put(b, (const char *)u, 1); u++; continue; }
        if (c < 0x20 || c == 0x7f) { snprintf(tmp, sizeof tmp, "\\u%04x", c); puts_(b, tmp); u++; continue; }
        if (c < 0x80) { put(b, (const char *)u, 1); u++; continue; }
        int n = (c & 0xe0) == 0xc0 ? 2 : (c & 0xf0) == 0xe0 ? 3 : (c & 0xf8) == 0xf0 ? 4 : 0;
        uint32_t cp = n == 2 ? (c & 0x1f) : n == 3 ? (c & 0x0f) : (c & 0x07);
        int ok = n != 0;
        for (int i = 1; ok && i < n; i++) {
            if ((u[i] & 0xc0) != 0x80) ok = 0; else cp = (cp << 6) | (u[i] & 0x3f);
        }
        if (ok && ((n == 2 && cp < 0x80) || (n == 3 && cp < 0x800) || (n == 4 && (cp < 0x10000 || cp > 0x10ffff))
                   || (cp >= 0xd800 && cp <= 0xdfff))) ok = 0;
        if (ok) { put(b, (const char *)u, (size_t)n); u += n; }
        else { puts_(b, "\xef\xbf\xbd"); u++; }
    }
    put(b, "\"", 1);
}

char *kep_rc_encode(unsigned window_id, const char *token, unsigned mods, const char *const *event,
                    size_t nargs, size_t *len) {
    if (!token || !event || nargs == 0 || nargs > MAX_ARGS || mods > 255) return NULL;
    for (size_t i = 0; i < nargs; i++) if (!event[i]) return NULL;
    buf b = {0};
    char tmp[64];
    puts_(&b, "\x1bP@kitty-cmd{\"cmd\":\"embed-input\",\"version\":[0,14,0],\"no_response\":true,\"payload\":{");
    snprintf(tmp, sizeof tmp, "\"match\":\"id:%u\",\"token\":", window_id);
    puts_(&b, tmp);
    put_json_string(&b, token);
    snprintf(tmp, sizeof tmp, ",\"mods\":%u,\"event\":[", mods);
    puts_(&b, tmp);
    for (size_t i = 0; i < nargs; i++) {
        if (i) put(&b, ",", 1);
        put_json_string(&b, event[i]);
    }
    puts_(&b, "]}}\x1b\\");
    if (b.bad) { free(b.p); return NULL; }
    if (len) *len = b.len;
    return b.p;
}

kep_rc *kep_rc_open(const char *listen_on, int *err) {
    if (!listen_on || strncmp(listen_on, "unix:", 5) || !listen_on[5]) { if (err) *err = KEP_ERR_ARG; return NULL; }
    const char *name = listen_on + 5;
    kep_rc *rc = calloc(1, sizeof *rc);
    if (!rc) { if (err) *err = KEP_ERR_NOMEM; return NULL; }
    rc->fd = -1;
    rc->addr.sun_family = AF_UNIX;
    size_t n = strlen(name);
    if (n >= sizeof rc->addr.sun_path) { free(rc); if (err) *err = KEP_ERR_ARG; return NULL; }
    if (name[0] == '@') {
        rc->addr.sun_path[0] = 0;
        memcpy(rc->addr.sun_path + 1, name + 1, n - 1);
        rc->addr_len = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + n);
    } else {
        memcpy(rc->addr.sun_path, name, n + 1);
        rc->addr_len = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + n + 1);
    }
    if (err) *err = KEP_OK;
    return rc;
}

void kep_rc_close(kep_rc *rc) {
    if (!rc) return;
    if (rc->fd >= 0) close(rc->fd);
    free(rc);
}

static int rc_connect(kep_rc *rc) {
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    if (connect(fd, (struct sockaddr *)&rc->addr, rc->addr_len)) { int e = errno; close(fd); errno = e; return -1; }
    rc->fd = fd;
    return 0;
}

static int send_all(int fd, const char *p, size_t n) {
    while (n) {
        ssize_t w = send(fd, p, n, MSG_NOSIGNAL);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        p += w; n -= (size_t)w;
    }
    return 0;
}

int kep_rc_input(kep_rc *rc, unsigned window_id, const char *token, unsigned mods,
                 const char *const *event, size_t nargs) {
    if (!rc) return KEP_ERR_ARG;
    size_t len;
    char *data = kep_rc_encode(window_id, token, mods, event, nargs, &len);
    if (!data) return KEP_ERR_ARG;
    int ret = KEP_ERR_IO;
    for (int attempt = 0; attempt < 2; attempt++) {
        if (rc->fd < 0 && rc_connect(rc)) continue;
        if (!send_all(rc->fd, data, len)) { ret = KEP_OK; break; }
        close(rc->fd); rc->fd = -1;
    }
    free(data);
    return ret;
}

int kep_rc_text(kep_rc *rc, unsigned window_id, const char *token, const char *utf8) {
    const char *ev[2] = {"text", utf8};
    return kep_rc_input(rc, window_id, token, 0, ev, 2);
}

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

int kep_rc_bytes(kep_rc *rc, unsigned window_id, const char *token, const void *data, size_t len) {
    const unsigned char *p = data;
    const size_t step = 48 * 1024;
    for (size_t at = 0; at < len; at += step) {
        size_t n = len - at < step ? len - at : step;
        char *enc = malloc(4 * ((n + 2) / 3) + 1);
        if (!enc) return KEP_ERR_NOMEM;
        size_t o = 0;
        for (size_t i = 0; i < n; i += 3) {
            uint32_t v = (uint32_t)p[at + i] << 16;
            if (i + 1 < n) v |= (uint32_t)p[at + i + 1] << 8;
            if (i + 2 < n) v |= p[at + i + 2];
            enc[o++] = B64[(v >> 18) & 63];
            enc[o++] = B64[(v >> 12) & 63];
            enc[o++] = i + 1 < n ? B64[(v >> 6) & 63] : '=';
            enc[o++] = i + 2 < n ? B64[v & 63] : '=';
        }
        enc[o] = 0;
        const char *ev[2] = {"bytes", enc};
        int r = kep_rc_input(rc, window_id, token, 0, ev, 2);
        free(enc);
        if (r) return r;
    }
    return KEP_OK;
}

int kep_rc_mouse(kep_rc *rc, unsigned window_id, const char *token, unsigned mods, int action, int button,
                 int cell_x, int cell_y, int px, int py, int left_half) {
    char a[7][16];
    int v[7] = {action, button, cell_x, cell_y, px, py, left_half ? 1 : 0};
    const char *ev[8] = {"mouse"};
    for (int i = 0; i < 7; i++) { snprintf(a[i], sizeof a[i], "%d", v[i]); ev[i + 1] = a[i]; }
    return kep_rc_input(rc, window_id, token, mods, ev, 8);
}

int kep_rc_scroll(kep_rc *rc, unsigned window_id, const char *token, unsigned mods, int lines,
                  int cell_x, int cell_y) {
    char a[3][16];
    int v[3] = {lines, cell_x, cell_y};
    const char *ev[4] = {"scroll"};
    for (int i = 0; i < 3; i++) { snprintf(a[i], sizeof a[i], "%d", v[i]); ev[i + 1] = a[i]; }
    return kep_rc_input(rc, window_id, token, mods, ev, 4);
}

int kep_rc_focus(kep_rc *rc, unsigned window_id, const char *token, int focused) {
    const char *ev[2] = {"focus", focused ? "in" : "out"};
    return kep_rc_input(rc, window_id, token, 0, ev, 2);
}
