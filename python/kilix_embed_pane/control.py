"""Asking the Kilix engine for embedded windows and sending them input.

Launching, resizing, closing and reading text are rare and go through the
engine's ``kitten @`` command with whatever credential the caller has
(Kilix's remote-control password file). Input is frequent and goes over one
long-lived connection to the engine's socket as ``embed-input`` commands
that carry the window's token and ask for no response.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

PREFIX, SUFFIX = b'\x1bP@kitty-cmd', b'\x1b\\'
# Kilix's engine accepts a command from any older protocol version.
PROTOCOL_VERSION = [0, 14, 0]
MAX_EVENT_ARGS = 66
# Mouse buttons as embed-input numbers them (the engine's GLFW numbers).
BUTTON_LEFT, BUTTON_RIGHT, BUTTON_MIDDLE, BUTTON_BACK, BUTTON_FORWARD = 0, 1, 2, 3, 4
# Mouse actions.
PRESS, RELEASE, MOVE = 0, 1, 2


class EmbedError(RuntimeError):
    pass


@dataclass
class EmbeddedWindow:
    id: int
    token: str
    map: str
    cols: int
    rows: int
    cell_width: int = 0
    cell_height: int = 0
    pid: int = 0

    @classmethod
    def from_response(cls, data: Mapping, token: str | None = None) -> 'EmbeddedWindow':
        try:
            return cls(int(data['id']), str(token if token is not None else data['token']), str(data['map']),
                       int(data['cols']), int(data['rows']), int(data.get('cell_width') or 0),
                       int(data.get('cell_height') or 0), int(data.get('pid') or 0))
        except (KeyError, TypeError, ValueError) as e:
            raise EmbedError(f'Unexpected answer from the engine: {e}') from None

    @property
    def match(self) -> str:
        return f'id:{self.id}'


def encode_command(cmd: str, payload: Mapping, no_response: bool = True) -> bytes:
    body = {'cmd': cmd, 'version': PROTOCOL_VERSION, 'no_response': no_response, 'payload': dict(payload)}
    return PREFIX + json.dumps(body, separators=(',', ':'), ensure_ascii=True).encode('ascii') + SUFFIX


def socket_address(listen_on: str):
    """unix:/path or unix:@abstract, as KITTY_LISTEN_ON names them."""
    if not listen_on.startswith('unix:'):
        raise EmbedError('Only unix sockets are supported')
    name = listen_on[5:]
    if not name:
        raise EmbedError('Empty socket name')
    return '\0' + name[1:] if name.startswith('@') else name


class InputConnection:
    """One socket connection to the engine for embed-input. send() never
    waits for an answer; a failed write reconnects once and retries."""

    def __init__(self, listen_on: str, timeout: float = 2.0) -> None:
        self.address = socket_address(listen_on)
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def _connect(self) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        s.settimeout(self.timeout)
        try:
            s.connect(self.address)
        except OSError:
            s.close()
            raise
        return s

    def send(self, window: EmbeddedWindow, event: Sequence[str], mods: int = 0) -> None:
        if not event or len(event) > MAX_EVENT_ARGS or not all(isinstance(x, str) for x in event):
            raise EmbedError('An event is a kind and up to 65 string arguments')
        data = encode_command('embed-input', {
            'match': window.match, 'token': window.token, 'mods': int(mods), 'event': list(event)})
        for attempt in (0, 1):
            try:
                if self.sock is None:
                    self.sock = self._connect()
                self.sock.sendall(data)
                return
            except OSError as e:
                self.close()
                if attempt:
                    raise EmbedError(f'Cannot reach the engine: {e}') from None

    def request(self, cmd: str, payload: Mapping, max_bytes: int = 8 << 20):
        """One command that answers, on this same connection: it reaches the
        engine after every event sent before it. Returns the answer's data."""
        data = encode_command(cmd, payload, no_response=False)
        for attempt in (0, 1):
            try:
                if self.sock is None:
                    self.sock = self._connect()
                self.sock.sendall(data)
                reply = bytearray()
                while SUFFIX not in reply:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        raise OSError('the engine closed the connection')
                    reply.extend(chunk)
                    if len(reply) > max_bytes:
                        raise OSError('the answer is too long')
                break
            except OSError as e:
                self.close()
                if attempt:
                    raise EmbedError(f'Cannot reach the engine: {e}') from None
        start = reply.find(PREFIX)
        end = reply.find(SUFFIX, start + 1)
        if start < 0 or end < 0:
            raise EmbedError('The engine answered in a form this does not read')
        try:
            answer = json.loads(bytes(reply[start + len(PREFIX):end]))
        except ValueError:
            raise EmbedError('The engine answered in a form this does not read') from None
        if not isinstance(answer, dict) or not answer.get('ok'):
            raise EmbedError(str(answer.get('error') if isinstance(answer, dict) else answer)[:400])
        return answer.get('data')

    def selection(self, window: EmbeddedWindow) -> str:
        """The window's selected text, after every mouse event sent before."""
        data = self.request('embed', {'match': window.match, 'token': window.token, 'size': '', 'cell_size': '',
                                      'selection': True})
        try:
            text = json.loads(data)['selection']
        except (TypeError, ValueError, KeyError):
            raise EmbedError('This engine does not answer with the selection') from None
        return text if isinstance(text, str) else ''

    def send_bytes(self, window: EmbeddedWindow, data: bytes) -> None:
        for at in range(0, len(data), 48 * 1024):
            self.send(window, ['bytes', base64.b64encode(data[at:at + 48 * 1024]).decode('ascii')])

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


class Engine:
    """The engine's ``kitten @`` with the caller's credential."""

    def __init__(self, listen_on: str, kitten: str = 'kitten', password_file: str | None = None,
                 env: Mapping[str, str] | None = None, timeout: float = 15.0) -> None:
        socket_address(listen_on)
        self.listen_on, self.kitten, self.password_file = listen_on, kitten, password_file
        self.env, self.timeout = env, timeout

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> 'Engine':
        env = os.environ if environ is None else environ
        listen_on = env.get('KITTY_LISTEN_ON') or ''
        if not listen_on:
            raise EmbedError('Not inside a Kilix window (no KITTY_LISTEN_ON)')
        kitten = env.get('KILIX_KITTEN') or ''
        if not kitten:
            launcher = env.get('KITTY_INSTALLATION_DIR')
            candidate = os.path.join(launcher, 'kitty', 'launcher', 'kitten') if launcher else ''
            kitten = candidate if candidate and os.access(candidate, os.X_OK) else (shutil.which('kitten') or 'kitten')
        return cls(listen_on, kitten, env.get('KILIX_RC_PASSWORD_FILE') or None, dict(env))

    def run(self, *args: str, password: bool = True) -> str:
        """kitten @ ARGS. Commands that carry the window's token (embed, and
        embed-input on the InputConnection) go without the password
        (password=False): the token is their credential, and kitty checks a
        command that does carry a password only against that password's
        list."""
        argv = [self.kitten, '@', '--to', self.listen_on]
        env = dict(os.environ if self.env is None else self.env)
        if password and self.password_file:
            argv += ['--password-file', self.password_file]
        if not password:
            for name in ('KITTY_RC_PASSWORD', 'KITTY_PUBLIC_KEY'):
                env.pop(name, None)
        try:
            p = subprocess.run([*argv, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=self.timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise EmbedError(f'kitten @ {args[0]} failed: {e}') from None
        if p.returncode:
            raise EmbedError(f'kitten @ {args[0]} failed: {(p.stderr or p.stdout).strip()[-400:]}')
        return p.stdout

    def supported(self) -> bool:
        """Whether this engine has embedded windows: its own kitten knows
        the embed command. Answered by the kitten alone, without the engine."""
        try:
            p = subprocess.run([self.kitten, '@', 'embed', '--help'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return p.returncode == 0

    def launch(self, argv: Sequence[str], cols: int, rows: int, cell_size: tuple[int, int] | None = None,
               env: Iterable[str] = (), cwd: str | None = None, title: str | None = None) -> EmbeddedWindow:
        args = ['launch', '--type=embed', f'--embed-size={int(cols)}x{int(rows)}']
        if cell_size:
            args.append(f'--embed-cell-size={int(cell_size[0])}x{int(cell_size[1])}')
        for item in env:
            args += ['--env', item]
        if cwd:
            args.append(f'--cwd={cwd}')
        if title:
            args.append(f'--title={title}')
        args += ['--', *argv]
        try:
            data = json.loads(self.run(*args))
        except ValueError:
            raise EmbedError('This Kilix engine does not support embedded windows') from None
        if not isinstance(data, dict):
            raise EmbedError('This Kilix engine does not support embedded windows')
        return EmbeddedWindow.from_response(data)

    def resize(self, window: EmbeddedWindow, cols: int, rows: int, cell_size: tuple[int, int] | None = None) -> None:
        args = ['embed', '--match', window.match, '--token', window.token, f'--size={int(cols)}x{int(rows)}']
        if cell_size:
            args.append(f'--cell-size={int(cell_size[0])}x{int(cell_size[1])}')
        data = json.loads(self.run(*args, password=False))
        window.cols, window.rows = int(data['cols']), int(data['rows'])

    def selection(self, window: EmbeddedWindow) -> str:
        return self.run('get-text', '--match', window.match, '--extent', 'selection')

    def close(self, window: EmbeddedWindow) -> None:
        self.run('close-window', '--match', window.match, '--ignore-no-match')

    def at_prompt(self, window: EmbeddedWindow) -> bool:
        """Whether the shell in the window sits at its prompt (shell integration)."""
        for osw in json.loads(self.run('ls', '--match', window.match)):
            for tab in osw.get('tabs', ()):
                for w in tab.get('windows', ()):
                    if w.get('id') == window.id:
                        return bool(w.get('at_prompt'))
        return False
