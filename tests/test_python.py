"""Tests for the Python side. Run: python3 -m unittest discover -s tests -p 'test_python.py'"""

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'python'))
sys.path.insert(0, HERE)

import kilix_embed_pane as kep  # noqa: E402
from kilix_embed_pane import control, kepmap  # noqa: E402
from kepm_writer import Writer, cell  # noqa: E402


class MapTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'w.kepm')
        self.w = Writer(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def frame(self, text='ab', **header):
        cells = [cell(c) for c in text]
        self.w.write(len(text), 1, cells, **header)

    def test_first_read_then_none_until_changed(self):
        self.frame('hi')
        with kep.MapReader(self.path) as r:
            f = r.read()
            self.assertEqual((f.cols, f.rows), (2, 1))
            self.assertEqual([chr(c.ch) for c in f.cells], ['h', 'i'])
            self.assertIsNone(r.read())
            self.frame('yo')
            self.assertEqual(chr(r.read().cells[0].ch), 'y')

    def test_dirty_rows_kept_when_no_frame_missed(self):
        self.w.write(2, 2, [cell('a')] * 4, dirty=[1, 1])
        r = kep.MapReader(self.path)
        r.read()
        self.w.write(2, 2, [cell('a')] * 4, dirty=[0, 1])
        self.assertEqual(r.read().dirty, bytes([0, 1]))
        r.close()

    def test_missed_frames_mark_every_row(self):
        self.w.write(2, 2, [cell('a')] * 4)
        r = kep.MapReader(self.path)
        r.read()
        self.w.write(2, 2, [cell('a')] * 4, dirty=[0, 0])
        self.w.write(2, 2, [cell('b')] * 4, dirty=[0, 1])
        self.assertEqual(r.read().dirty, bytes([1, 1]))
        r.close()

    def test_frame_being_written_is_not_returned(self):
        self.frame('ok')
        self.w.write(2, 1, [cell('x'), cell('y')], odd=True)
        r = kep.MapReader(self.path)
        with self.assertRaises(kep.MapError):
            r.read(tries=5)
        r.close()

    def test_header_fields(self):
        self.frame('ab', cursor_x=1, cursor_y=0, cursor_shape=1, modes=4 | 1, mouse_mode=2, mouse_proto=2,
                   history_rows=40, view_offset=3, title='héllo')
        f = kep.MapReader(self.path).read()
        self.assertEqual(f.cursor[:3], (1, 0, 1))
        self.assertTrue(f.modes & kep.MODE_ALTERNATE_SCREEN and f.modes & kep.MODE_BRACKETED_PASTE)
        self.assertEqual((f.mouse_tracking_mode, f.mouse_tracking_protocol), (2, 2))
        self.assertEqual((f.history_rows, f.view_offset), (40, 3))
        self.assertEqual(f.title, 'héllo')

    def test_rejects_foreign_and_short_files(self):
        bad = os.path.join(self.dir.name, 'bad')
        with open(bad, 'wb') as fh:
            fh.write(b'NOPE' + bytes(400))
        with self.assertRaises(kep.MapError):
            kep.MapReader(bad)
        with open(bad, 'wb') as fh:
            fh.write(b'KEPM')
        with self.assertRaises(kep.MapError):
            kep.MapReader(bad)

    def test_out_of_range_size_refused(self):
        self.w.write(2, 1, [cell('a'), cell('b')])
        self.w.fields['cols'] = 9999
        with open(self.path, 'r+b') as fh:
            fh.write(self.w._pack())
        with self.assertRaises(kep.MapError):
            kep.MapReader(self.path).read(force=True)

    def test_no_frame_yet(self):
        r = kep.MapReader(self.path)
        self.assertIsNone(r.read())
        self.assertIsNone(r.read(force=True))
        r.close()

    def test_symlink_refused(self):
        self.frame('ab')
        link = os.path.join(self.dir.name, 'link')
        os.symlink(self.path, link)
        with self.assertRaises(OSError):
            kep.MapReader(link)


class RecordTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'w.kepm')
        self.w = Writer(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_flags_and_colours(self):
        cells = [
            cell('B', fg=0xff0000, attrs=kep.ATTR_BOLD | kep.ATTR_ITALIC),
            cell('u', underline=3),
            cell('s', attrs=kep.ATTR_STRIKE | kep.ATTR_DIM),
            cell('i', attrs=kep.ATTR_INVISIBLE | kep.ATTR_OVERLINE),
            cell(0x4e2d, attrs=kep.ATTR_WIDE), cell(0, attrs=kep.ATTR_WIDE_TAIL),
            cell(0),
        ]
        self.w.write(7, 1, cells)
        recs = kep.to_records(kep.MapReader(self.path).read())
        self.assertEqual(recs[0], (ord('B'), 0xff0000, 0, 1 | 2))
        self.assertEqual(recs[1][3], 4)
        self.assertEqual(recs[2][3], 8 | 32)
        self.assertEqual(recs[3][3], 64 | 128)
        self.assertEqual(recs[4][0], 0x4e2d)
        self.assertEqual(recs[5][0], 32)
        self.assertEqual(recs[6][0], 32)
        self.assertFalse(any(r[3] & 16 for r in recs))

    def test_combining_marks(self):
        # e + combining acute composes to é; a + two marks does not compose fully.
        self.w.write(2, 1, [cell('e', extra=1), cell('q', extra=3)], combining=[1, 0x301, 2, 0x301, 0x302])
        f = kep.MapReader(self.path).read()
        self.assertEqual(kep.cell_text(f, 0), 'é')
        recs = kep.to_records(f)
        self.assertEqual(recs[0], (0xe9, 0xdddddd, 0, 0))
        self.assertEqual(recs[1][0], ord('q'))
        self.assertTrue(recs[1][3] & 256)


class Server:
    """A unix socket that records what arrives."""

    def __init__(self, name):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind('\0' + name)
        self.sock.listen(4)
        self.data, self.conns = bytearray(), 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns += 1
            with c:
                while chunk := c.recv(65536):
                    self.data.extend(chunk)

    def commands(self):
        out, raw = [], bytes(self.data)
        while raw:
            assert raw.startswith(control.PREFIX), raw[:20]
            end = raw.index(control.SUFFIX)
            out.append(json.loads(raw[len(control.PREFIX):end]))
            raw = raw[end + len(control.SUFFIX):]
        return out

    def close(self):
        self.sock.close()


class InputTests(unittest.TestCase):

    def test_commands_on_one_connection(self):
        name = f'kep-test-{os.getpid()}-{id(self)}'
        server = Server(name)
        conn = kep.InputConnection(f'unix:@{name}')
        win = kep.EmbeddedWindow(7, 'ab' * 16, '/x', 80, 24)
        conn.send(win, ['text', 'ls\r'])
        conn.send(win, ['mouse', '0', '0', '3', '4', '24', '64', '1'], mods=1)
        conn.send_bytes(win, b'\x1b[A\xff')
        conn.close()
        for _ in range(100):
            if len(server.commands()) == 3:
                break
            threading.Event().wait(0.02)
        cmds = server.commands()
        server.close()
        self.assertEqual(server.conns, 1)
        self.assertEqual([c['cmd'] for c in cmds], ['embed-input'] * 3)
        self.assertTrue(all(c['no_response'] for c in cmds))
        self.assertEqual(cmds[0]['payload'], {'match': 'id:7', 'token': 'ab' * 16, 'mods': 0, 'event': ['text', 'ls\r']})
        self.assertEqual(cmds[1]['payload']['mods'], 1)
        self.assertEqual(cmds[2]['payload']['event'], ['bytes', 'G1tB/w=='])

    def test_request_reads_the_answer_on_the_same_connection(self):
        name = f'kep-answer-{os.getpid()}'
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind('\0' + name)
        srv.listen(1)
        got = bytearray()

        def serve():
            c, _ = srv.accept()
            with c:
                while control.SUFFIX not in got or got.count(control.SUFFIX) < 2:
                    got.extend(c.recv(65536))
                reply = {'ok': True, 'data': json.dumps({'id': 7, 'selection': 'picked'})}
                c.sendall(control.PREFIX + json.dumps(reply).encode() + control.SUFFIX)
                c.recv(1)
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        conn = kep.InputConnection(f'unix:@{name}')
        win = kep.EmbeddedWindow(7, 'ab' * 16, '/x', 80, 24)
        conn.send(win, ['mouse', '1', '0', '1', '1'])
        self.assertEqual(conn.selection(win), 'picked')
        conn.close()
        srv.close()
        first, second = bytes(got).split(control.SUFFIX)[:2]
        self.assertIn(b'"mouse"', first)       # the release went first, on the same connection
        body = json.loads(second[len(control.PREFIX):])
        self.assertEqual((body['cmd'], body['no_response'], body['payload']['selection']), ('embed', False, True))

    def test_bad_events_refused(self):
        conn = kep.InputConnection('unix:@nobody-listens-here')
        win = kep.EmbeddedWindow(1, 't', '/x', 2, 2)
        for bad in ([], ['text'] * 67, ['text', 3]):
            with self.assertRaises(kep.EmbedError):
                conn.send(win, bad)

    def test_unreachable_engine(self):
        conn = kep.InputConnection(f'unix:@kep-nothing-{os.getpid()}')
        with self.assertRaises(kep.EmbedError):
            conn.send(kep.EmbeddedWindow(1, 't', '/x', 2, 2), ['text', 'x'])

    def test_socket_addresses(self):
        self.assertEqual(control.socket_address('unix:@abc'), '\0abc')
        self.assertEqual(control.socket_address('unix:/run/x.sock'), '/run/x.sock')
        for bad in ('tcp:localhost:1', 'unix:'):
            with self.assertRaises(kep.EmbedError):
                control.socket_address(bad)


class EngineTests(unittest.TestCase):
    """Engine drives `kitten @`; a stand-in kitten records its arguments."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.dir.name, 'args')
        self.kitten = os.path.join(self.dir.name, 'kitten')
        self.answer = os.path.join(self.dir.name, 'answer')
        with open(self.kitten, 'w') as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n" "$@" > {self.log}\ncat {self.answer}\n')
        os.chmod(self.kitten, stat.S_IRWXU)

    def tearDown(self):
        self.dir.cleanup()

    def args(self):
        with open(self.log) as f:
            return f.read().splitlines()

    def test_launch(self):
        with open(self.answer, 'w') as f:
            json.dump({'id': 5, 'map': '/r/5.kepm', 'cols': 80, 'rows': 24, 'cell_width': 8, 'cell_height': 16,
                       'token': 'f' * 32}, f)
        e = kep.Engine('unix:@x', self.kitten, '/p/pw')
        w = e.launch(['bash', '-l'], 80, 24, (8, 16), env=['A=1'], cwd='/home')
        self.assertEqual((w.id, w.token, w.map, w.cols, w.rows), (5, 'f' * 32, '/r/5.kepm', 80, 24))
        a = self.args()
        self.assertEqual(a[:5], ['@', '--to', 'unix:@x', '--password-file', '/p/pw'])
        self.assertIn('--type=embed', a)
        self.assertIn('--embed-size=80x24', a)
        self.assertIn('--embed-cell-size=8x16', a)
        self.assertEqual(a[-3:], ['--', 'bash', '-l'])

    def test_supported_asks_the_kitten_only(self):
        with open(self.answer, 'w') as f:
            f.write('')
        self.assertTrue(kep.Engine('unix:@x', self.kitten).supported())
        self.assertEqual(self.args(), ['@', 'embed', '--help'])
        self.assertFalse(kep.Engine('unix:@x', os.path.join(self.dir.name, 'missing')).supported())

    def test_old_engine_answer(self):
        with open(self.answer, 'w') as f:
            f.write('12\n')
        with self.assertRaises(kep.EmbedError):
            kep.Engine('unix:@x', self.kitten).launch(['sh'], 10, 5)

    def test_resize(self):
        with open(self.answer, 'w') as f:
            json.dump({'id': 5, 'map': '/m', 'cols': 100, 'rows': 30}, f)
        w = kep.EmbeddedWindow(5, 'tok', '/m', 80, 24)
        kep.Engine('unix:@x', self.kitten, '/p/pw').resize(w, 100, 30)
        self.assertEqual((w.cols, w.rows), (100, 30))
        self.assertEqual(self.args(), ['@', '--to', 'unix:@x', 'embed', '--match', 'id:5', '--token', 'tok',
                                       '--size=100x30'])

    def test_failure_is_an_embed_error(self):
        with open(self.kitten, 'w') as f:
            f.write('#!/bin/sh\necho denied >&2\nexit 1\n')
        with self.assertRaises(kep.EmbedError):
            kep.Engine('unix:@x', self.kitten).close(kep.EmbeddedWindow(1, 't', '/m', 2, 2))


if __name__ == '__main__':
    unittest.main()
