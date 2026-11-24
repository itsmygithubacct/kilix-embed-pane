"""The module against a real engine (run through run.sh, which sets KEP_*)."""

import ctypes as C
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'python'))

import kilix_embed_pane as kep  # noqa: E402

SOCK = os.environ['KEP_SOCK']
ENGINE = kep.Engine(SOCK, os.environ['KEP_KITTEN'], os.environ.get('KEP_PASSWORD_FILE') or None)


class CCell(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('ch', 'extra', 'fg', 'bg', 'ul_color', 'link')] + \
        [('attrs', C.c_uint16), ('underline', C.c_uint8), ('reserved', C.c_uint8)]


class CCursor(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('shape', C.c_uint8), ('visible', C.c_uint8),
                ('blink', C.c_uint8), ('focused', C.c_uint8), ('color', C.c_uint32)]


class CGrid(C.Structure):
    _fields_ = [('cols', C.c_int), ('rows', C.c_int), ('cells', C.POINTER(CCell)),
                ('combining', C.POINTER(C.c_uint32)), ('combining_len', C.c_size_t), ('cursor', CCursor),
                ('default_fg', C.c_uint32), ('default_bg', C.c_uint32), ('dirty_rows', C.POINTER(C.c_uint8)),
                ('history_rows', C.c_int), ('view_offset', C.c_int)]


LIB = C.CDLL(os.environ['KEP_LIB'])
LIB.kep_map_open.restype = C.c_void_p
LIB.kep_map_open.argtypes = [C.c_char_p, C.POINTER(C.c_int)]
LIB.kep_map_read.argtypes = [C.c_void_p, C.POINTER(CGrid), C.c_int]
LIB.kep_map_close.argtypes = [C.c_void_p]
LIB.kep_rc_open.restype = C.c_void_p
LIB.kep_rc_open.argtypes = [C.c_char_p, C.POINTER(C.c_int)]
LIB.kep_rc_text.argtypes = [C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p]
LIB.kep_rc_close.argtypes = [C.c_void_p]


def rows_text(frame):
    return [''.join(kep.cell_text(frame, y * frame.cols + x) for x in range(frame.cols)).rstrip()
            for y in range(frame.rows)]


def wait_for(reader, test, seconds=8.0):
    deadline = time.monotonic() + seconds
    frame = last = None
    while time.monotonic() < deadline:
        frame = reader.read() or last
        last = frame
        if frame is not None and test(frame):
            return frame
        time.sleep(0.03)
    raise AssertionError(f'timed out; last screen: {rows_text(last) if last else None}')


class EngineTests(unittest.TestCase):

    def setUp(self):
        self.win = ENGINE.launch(['bash', '--norc', '--noprofile'], 50, 12, (8, 16),
                                 env=['PS1=> ', 'HISTFILE=/dev/null'])
        self.reader = kep.MapReader(self.win.map)
        self.conn = kep.InputConnection(SOCK)

    def tearDown(self):
        self.conn.close()
        self.reader.close()
        try:
            ENGINE.close(self.win)
        except kep.EmbedError:
            pass

    def type(self, text):
        self.conn.send(self.win, ['text', text])

    def test_launch_answer_and_map_place(self):
        self.assertEqual((self.win.cols, self.win.rows), (50, 12))
        self.assertTrue(self.win.map.startswith(os.environ['KEP_RUNTIME']))
        self.assertEqual(len(self.win.token), 32)
        self.assertEqual(os.stat(self.win.map).st_mode & 0o777, 0o600)
        f = wait_for(self.reader, lambda f: f.rows == 12)
        self.assertEqual(f.cols, 50)

    def test_typed_text_and_title(self):
        self.type('echo kep-$((6*7))\r')
        f = wait_for(self.reader, lambda f: any(r == 'kep-42' for r in rows_text(f)))
        self.type("printf '\\033]2;renamed\\007'\r")
        wait_for(self.reader, lambda f: f.title == 'renamed')

    def test_bytes_kind(self):
        self.conn.send_bytes(self.win, b'echo by\x74es-ok\r')
        wait_for(self.reader, lambda f: 'bytes-ok' in rows_text(f))

    def test_c_reader_and_c_client_agree(self):
        err = C.c_int()
        rc = LIB.kep_rc_open(SOCK.encode(), C.byref(err))
        self.assertTrue(rc)
        self.assertEqual(LIB.kep_rc_text(rc, self.win.id, self.win.token.encode(), b'echo from-c\r'), 0)
        LIB.kep_rc_close(rc)
        f = wait_for(self.reader, lambda f: 'from-c' in rows_text(f))
        m = LIB.kep_map_open(self.win.map.encode(), C.byref(err))
        self.assertTrue(m, err.value)
        grid = CGrid()
        self.assertIn(LIB.kep_map_read(m, C.byref(grid), 1), (0,))
        text = [''.join(chr(grid.cells[y * grid.cols + x].ch or 32) for x in range(grid.cols)).rstrip()
                for y in range(grid.rows)]
        LIB.kep_map_close(m)
        self.assertIn('from-c', text)
        self.assertEqual((grid.cols, grid.rows), (f.cols, f.rows))

    def test_wrong_token_does_nothing(self):
        bad = kep.EmbeddedWindow(self.win.id, '0' * 32, self.win.map, 50, 12)
        self.conn.send(bad, ['text', 'echo should-not-run\r'])
        self.type('echo marker-after\r')
        f = wait_for(self.reader, lambda f: 'marker-after' in rows_text(f))
        self.assertNotIn('should-not-run', '\n'.join(rows_text(f)))

    def test_resize_reaches_the_program(self):
        ENGINE.resize(self.win, 64, 20)
        self.assertEqual((self.win.cols, self.win.rows), (64, 20))
        self.type('stty size\r')
        wait_for(self.reader, lambda f: f.cols == 64 and f.rows == 20 and '20 64' in rows_text(f))

    def test_scrollback_by_wheel(self):
        self.type('seq 1 200\r')
        wait_for(self.reader, lambda f: '200' in rows_text(f))
        self.conn.send(self.win, ['scroll', '30', '1', '1'])
        f = wait_for(self.reader, lambda f: f.view_offset == 30)
        self.assertGreaterEqual(f.history_rows, 180)
        self.type('x')   # typing returns to the live rows
        wait_for(self.reader, lambda f: f.view_offset == 0)

    def test_selection_by_drag_and_get_text(self):
        self.type('clear; echo pick-this-word\r')
        f = wait_for(self.reader, lambda f: 'pick-this-word' in rows_text(f))
        y = rows_text(f).index('pick-this-word')
        # Press in the left half of the first cell, release in the right half of the last.
        for action, x, half in ((kep.PRESS, 0, 1), (kep.MOVE, 6, 1), (kep.RELEASE, 13, 0)):
            self.conn.send(self.win, ['mouse', str(action), str(kep.BUTTON_LEFT), str(x), str(y),
                                      str(x * 8 + (2 if half else 6)), str(y * 16 + 8), str(half)])
        wait_for(self.reader, lambda f: any(c.attrs & kep.ATTR_SELECTED for c in f.cells))
        self.assertEqual(ENGINE.selection(self.win).strip(), 'pick-this-word')

    @unittest.skipUnless(os.environ.get('KEP_PASSWORD_FILE'), 'only under the Kilix config')
    def test_under_kilix_config_token_calls_need_no_password(self):
        # Kilix accepts socket commands unconditionally (allow_remote_control
        # socket); this proves the password-carrying launch above and the
        # token-only resize and input both get through its real config.
        bare = kep.Engine(SOCK, os.environ['KEP_KITTEN'])
        bare.resize(self.win, 40, 10)
        self.assertEqual((self.win.cols, self.win.rows), (40, 10))
        self.type('stty size\r')
        wait_for(self.reader, lambda f: '10 40' in rows_text(f))

    def test_map_removed_on_close(self):
        path = self.win.map
        ENGINE.close(self.win)
        deadline = time.monotonic() + 5
        while os.path.exists(path) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(os.path.exists(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
