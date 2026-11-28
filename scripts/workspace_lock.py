"""Linux task-scoped lock for AI_LOOP agents that execute SKILL.md."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys


def inherited(identity):
    """Recognize an enclosing scheduler or relay even if it kept the fd itself."""
    pid = os.getppid()
    while pid > 1:
        proc = Path('/proc') / str(pid)
        try:
            for fd in (proc / 'fd').iterdir():
                try:
                    if os.path.samestat(fd.stat(), identity):
                        info = (proc / 'fdinfo' / fd.name).read_text()
                        if any(line.startswith('lock:') and line.split()[2:5] ==
                               ['FLOCK', 'ADVISORY', 'WRITE'] for line in info.splitlines()):
                            return True
                except OSError:
                    continue
        except PermissionError:
            pass
        pid = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[1])
    return False


def address(identity, ticket):
    name = f'{identity.st_dev}:{identity.st_ino}:{ticket}'.encode()
    return '\0ai-loop-' + hashlib.sha256(name).hexdigest()


def contact(addr, command):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        try:
            client.connect(addr)
        except ConnectionRefusedError:
            return False
        client.sendall(command)
        if client.recv(2) != b'OK':
            raise RuntimeError('锁持有进程没有确认请求')
        return True


def hold(listener_fd, lock_fd, owner_fd):
    # With no owner supplied, retain the lock until its task explicitly ends.
    with socket.socket(fileno=listener_fd) as listener:
        while True:
            watched = [listener] if owner_fd < 0 else [listener, owner_fd]
            ready, _, _ = select.select(watched, [], [])
            if owner_fd in ready:
                return
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(2)
                try:
                    cmd = conn.recv(1)
                    if cmd == b'R':
                        os.close(lock_fd)
                        try:
                            conn.sendall(b'OK')
                        except OSError:
                            pass
                        return
                    if cmd == b'Q':
                        conn.sendall(b'OK')
                except OSError:
                    continue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enter', 'leave', '_hold'))
    parser.add_argument('ticket', nargs='?')
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--fds', nargs=3, type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == '_hold':
        return hold(*args.fds)
    path = Path(__file__).resolve().parents[1] / 'data/.pr-review-cycle.lock'
    if args.action == 'leave' and args.ticket == 'inherited':
        return
    ticket = args.ticket or (secrets.token_hex(16) if args.action == 'enter' else '')
    if len(ticket) != 32 or any(ch not in '0123456789abcdef' for ch in ticket):
        parser.error('需要 enter 返回的任务票据')
    path.parent.mkdir(exist_ok=True)
    with path.open('a') as lock:
        identity = os.fstat(lock.fileno())
        addr = address(identity, ticket)
        if args.action == 'leave':
            contact(addr, b'R')  # Repeating a completed release is harmless.
            return
        if args.ticket and contact(addr, b'Q'):
            print(ticket)
            return
        if inherited(identity):
            print('inherited')
            return
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('AI_LOOP 工作区正在使用。本轮尚未开始，请在锁释放后重试。')
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(addr)
            listener.listen()
            owner_fd = -1 if args.owner_pid is None else os.pidfd_open(args.owner_pid)
            fds = [listener.fileno(), lock.fileno(), owner_fd]
            try:
                subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                                  '_hold', '--fds', *map(str, fds)], pass_fds=tuple(n for n in fds if n >= 0),
                                 start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            finally:
                if owner_fd >= 0:
                    os.close(owner_fd)
    if not contact(addr, b'Q'):
        raise RuntimeError('未能确认工作区锁，任务不能开始')
    print(ticket)


if __name__ == '__main__':
    main()
