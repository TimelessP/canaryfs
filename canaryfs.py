#!/usr/bin/env python3
"""
CanaryFS: a 10MB in-memory FUSE filesystem with optional interactive access control.

Key features:
- Userspace filesystem via fusepy (Linux).
- 10MB volatile storage. Data is lost on unmount.
- Logs all operations.
- Optional interactive prompts (--ask) to allow/deny operations per path with
  time-based or count-based temporary allowances.
- Graceful unmount on Ctrl-C.

Notes:
- This does NOT format ext4. FUSE filesystems implement the VFS callbacks directly and
  don't require a block device or kernel filesystem like ext4. You just mount this
  userspace FS at a directory. No root required (ensure FUSE is installed and your user
  is in the 'fuse' group).
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import signal
import stat
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from fuse import FUSE, FuseOSError, Operations
except Exception as e:  # pragma: no cover - import-time guidance
    print("fusepy is required. Install with: pip install fusepy", file=sys.stderr)
    raise


BLOCK_SIZE = 4096
DEFAULT_CAPACITY = 10 * 1024 * 1024  # 10MB


def now_ts() -> float:
    return time.time()


@dataclass
class Rule:
    # Allow rule for (op, path)
    expires_at: Optional[float] = None
    remaining: Optional[int] = None

    def allowed(self) -> bool:
        if self.expires_at is not None:
            return now_ts() <= self.expires_at
        if self.remaining is not None:
            return self.remaining > 0
        # Single-shot implicit allowance if neither set (handled by caller)
        return True

    def consume(self) -> None:
        if self.remaining is not None and self.remaining > 0:
            self.remaining -= 1


class Node:
    def __init__(self, is_dir: bool, mode: int):
        self.is_dir = is_dir
        self.mode = mode
        self.nlink = 2 if is_dir else 1
        self.size = 0
        ts = now_ts()
        self.atime = ts
        self.mtime = ts
        self.ctime = ts
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.children: Dict[str, Node] = {} if is_dir else {}
        self.data = bytearray() if not is_dir else bytearray()


class CanaryFS(Operations):
    def __init__(self, capacity: int = DEFAULT_CAPACITY, ask: bool = True, ask_scope: str = 'op'):
        self.capacity = capacity
        self.ask = ask
        # ask_scope: 'op' => rules are per (op, path); 'path' => rules are per (*, path)
        self.ask_scope = ask_scope
        # When set True via prompt input 'a', all operations are allowed without further prompts
        # for the remainder of the mount session (logging still occurs).
        self.global_allow_all = False
        self.used = 0
        self.fd = 0
        self.files = {}  # type: Dict[str, Node]
        self.allow_rules = {}  # type: Dict[Tuple[str, str], Rule]
        # root directory
        self.files['/'] = Node(True, stat.S_IFDIR | 0o755)
        self.log = logging.getLogger('canaryfs')

    # ---------- helpers ----------
    def _split(self, path: str) -> Tuple[str, str]:
        parent, name = os.path.split(path)
        if not parent:
            parent = '/'
        return parent, name

    def _get(self, path: str) -> Node:
        node = self.files.get(path)
        if node is None:
            raise FuseOSError(errno.ENOENT)
        return node

    def _ensure_parent(self, path: str) -> Tuple[Node, str]:
        parent, name = self._split(path)
        pnode = self._get(parent)
        if not pnode.is_dir:
            raise FuseOSError(errno.ENOTDIR)
        return pnode, name

    def _ensure_space_delta(self, delta: int) -> None:
        if delta <= 0:
            return
        if self.used + delta > self.capacity:
            raise FuseOSError(errno.ENOSPC)

    def _rule_key(self, op: str, path: str) -> Tuple[str, str]:
        if self.ask_scope == 'path':
            return ('*', path)
        return (op, path)

    def _check_and_prompt(self, op: str, path: str) -> None:
        """Check allow rules and optionally prompt. Raise FuseOSError(EACCES) if denied."""
        # Logging first
        self.log.info("op=%s path=%s", op, path)

        if not self.ask or self.global_allow_all:
            return

        key = self._rule_key(op, path)
        rule = self.allow_rules.get(key)
        if rule:
            if rule.allowed():
                rule.consume()
                return
            else:
                # expired => remove
                self.allow_rules.pop(key, None)

        # Prompt
        while True:
            try:
                ans = input(f"[canaryfs] Allow {op} {path}? (Y/n/a or <Ns>/<N>): ").strip()
            except EOFError:
                ans = 'n'

            if ans == '' or ans.lower() == 'y':
                # Allow once
                return
            if ans.lower() == 'a':
                # Allow all future operations (no more prompts this session)
                self.global_allow_all = True
                return
            if ans.lower() == 'n':
                raise FuseOSError(errno.EACCES)
            if ans.endswith('s') and ans[:-1].isdigit():
                seconds = int(ans[:-1])
                self.allow_rules[key] = Rule(expires_at=now_ts() + seconds)
                return
            if ans.isdigit():
                count = int(ans)
                self.allow_rules[key] = Rule(remaining=count)
                return
            print("Enter Y, n, a (allow all), e.g. 10s for seconds, or 10 for count.")

    # ---------- FUSE operations ----------
    def access(self, path, mode):
        self._check_and_prompt('access', path)
        node = self._get(path)
        # Very permissive: if exists, allow; real permission checks omitted
        return 0

    def getattr(self, path, fh=None):
        self._check_and_prompt('getattr', path)
        node = self._get(path)
        st = dict(
            st_mode=node.mode,
            st_nlink=node.nlink,
            st_size=node.size,
            st_ctime=node.ctime,
            st_mtime=node.mtime,
            st_atime=node.atime,
            st_uid=node.uid,
            st_gid=node.gid,
        )
        return st

    def readdir(self, path, fh):
        self._check_and_prompt('readdir', path)
        node = self._get(path)
        if not node.is_dir:
            raise FuseOSError(errno.ENOTDIR)
        entries = ['.', '..'] + sorted(node.children.keys())
        return entries

    def mkdir(self, path, mode):
        self._check_and_prompt('mkdir', path)
        parent_node, name = self._ensure_parent(path)
        if name in parent_node.children:
            raise FuseOSError(errno.EEXIST)
        node = Node(True, stat.S_IFDIR | (mode & 0o777))
        parent_node.children[name] = node
        self.files[path] = node
        parent_node.nlink += 1
        parent_node.mtime = parent_node.ctime = now_ts()

    def rmdir(self, path):
        self._check_and_prompt('rmdir', path)
        node = self._get(path)
        if not node.is_dir:
            raise FuseOSError(errno.ENOTDIR)
        if node.children:
            raise FuseOSError(errno.ENOTEMPTY)
        parent_node, name = self._ensure_parent(path)
        del parent_node.children[name]
        del self.files[path]
        parent_node.nlink -= 1
        parent_node.mtime = parent_node.ctime = now_ts()

    def unlink(self, path):
        self._check_and_prompt('unlink', path)
        node = self._get(path)
        if node.is_dir:
            raise FuseOSError(errno.EISDIR)
        parent_node, name = self._ensure_parent(path)
        del parent_node.children[name]
        del self.files[path]
        self.used -= node.size
        parent_node.mtime = parent_node.ctime = now_ts()

    def rename(self, old, new):
        self._check_and_prompt('rename', old)
        self._check_and_prompt('rename', new)
        node = self._get(old)
        old_parent, old_name = self._ensure_parent(old)
        new_parent, new_name = self._ensure_parent(new)
        if new_name in new_parent.children:
            # Follow POSIX: if target exists and is file, unlink it
            target = new_parent.children[new_name]
            if target.is_dir:
                raise FuseOSError(errno.EISDIR)
            del new_parent.children[new_name]
            del self.files[new]
            self.used -= target.size
        del old_parent.children[old_name]
        new_parent.children[new_name] = node
        del self.files[old]
        self.files[new] = node
        now = now_ts()
        old_parent.mtime = old_parent.ctime = now
        new_parent.mtime = new_parent.ctime = now

    def open(self, path, flags):
        self._check_and_prompt('open', path)
        self._get(path)  # ensure exists
        self.fd += 1
        return self.fd

    def create(self, path, mode, fi=None):
        self._check_and_prompt('create', path)
        parent_node, name = self._ensure_parent(path)
        if name in parent_node.children:
            raise FuseOSError(errno.EEXIST)
        node = Node(False, stat.S_IFREG | (mode & 0o777))
        parent_node.children[name] = node
        self.files[path] = node
        parent_node.mtime = parent_node.ctime = now_ts()
        self.fd += 1
        return self.fd

    def read(self, path, size, offset, fh):
        self._check_and_prompt('read', path)
        node = self._get(path)
        if node.is_dir:
            raise FuseOSError(errno.EISDIR)
        data = bytes(node.data[offset: offset + size])
        node.atime = now_ts()
        return data

    def write(self, path, data, offset, fh):
        self._check_and_prompt('write', path)
        node = self._get(path)
        if node.is_dir:
            raise FuseOSError(errno.EISDIR)
        data = memoryview(data).tobytes()
        endpos = offset + len(data)
        # Ensure capacity
        prev_size = node.size
        new_size = max(prev_size, endpos)
        delta = new_size - prev_size
        self._ensure_space_delta(delta)
        if new_size > len(node.data):
            node.data.extend(b"\x00" * (new_size - len(node.data)))
        node.data[offset:endpos] = data
        node.size = new_size
        self.used += delta
        ts = now_ts()
        node.mtime = ts
        node.atime = ts
        return len(data)

    def truncate(self, path, length, fh=None):
        self._check_and_prompt('truncate', path)
        node = self._get(path)
        if node.is_dir:
            raise FuseOSError(errno.EISDIR)
        length = int(length)
        if length < node.size:
            delta = node.size - length
            node.data = node.data[:length]
            node.size = length
            self.used -= delta
        elif length > node.size:
            delta = length - node.size
            self._ensure_space_delta(delta)
            node.data.extend(b"\x00" * delta)
            node.size = length
            self.used += delta
        node.mtime = now_ts()

    def utimens(self, path, times=None):
        self._check_and_prompt('utimens', path)
        node = self._get(path)
        atime, mtime = times if times else (now_ts(), now_ts())
        node.atime = atime
        node.mtime = mtime

    def chmod(self, path, mode):
        self._check_and_prompt('chmod', path)
        node = self._get(path)
        node.mode = (node.mode & ~0o777) | (mode & 0o777)
        node.ctime = now_ts()
        return 0

    def chown(self, path, uid, gid):
        self._check_and_prompt('chown', path)
        node = self._get(path)
        node.uid = uid
        node.gid = gid
        node.ctime = now_ts()
        return 0

    def statfs(self, path):
        # No prompt; statfs can be frequent, but we still log
        self.log.info("op=statfs path=%s", path)
        blocks_total = self.capacity // BLOCK_SIZE
        blocks_free = (self.capacity - self.used) // BLOCK_SIZE
        return {
            'f_bsize': BLOCK_SIZE,
            'f_frsize': BLOCK_SIZE,
            'f_blocks': blocks_total,
            'f_bfree': blocks_free,
            'f_bavail': blocks_free,
            'f_namemax': 255,
        }


def ensure_mountpoint(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    if not os.path.isdir(path):
        print(f"Mountpoint is not a directory: {path}", file=sys.stderr)
        sys.exit(2)


def unmount(mountpoint: str) -> None:
    # Try fusermount3 then fusermount
    for cmd in ("fusermount3 -u", "fusermount -u"):
        rc = os.system(f"{cmd} '{mountpoint}' >/dev/null 2>&1")
        if rc == 0:
            return


def main(argv: Optional[List[str]] = None) -> int:
    class _HelpFmt(argparse.RawTextHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
        pass

    parser = argparse.ArgumentParser(
        description=(
            "CanaryFS: 10MB in-memory FUSE filesystem (FUSE, userspace).\n\n"
            "Prompts (when --ask) appear for each filesystem operation. Respond with:\n"
            "  - Y or Enter: allow once\n"
            "  - n: deny\n"
            "  - 10s: allow for 10 seconds\n"
            "  - 1000: allow the next 1000 times\n"
            "  - a: allow all operations from now on (no further prompts this session)\n\n"
            "Scope of allowances (--ask-scope):\n"
            "  - op: applies to the specific operation and exact path (e.g., read on /mnt/file).\n"
            "  - path: applies to all operations on the exact path (any op on /mnt/file).\n\n"
            "Notes: allowances are for exact paths only (no wildcards/subtrees).\n"
            "Renames create a new path, so previous allowances may not apply."
        ),
        formatter_class=_HelpFmt,
    )
    parser.add_argument('--mount', required=True, help='Mountpoint directory')
    parser.add_argument('--no-ask', dest='ask', action='store_false', help='Disable interactive prompts (log only)')
    parser.add_argument('--ask', dest='ask', action='store_true', help='Enable interactive prompts')
    parser.set_defaults(ask=True)
    parser.add_argument('--ask-scope', choices=['op', 'path'], default='op',
                        help=(
                            "How to key allowances from prompts:\n"
                            "  op   - per (operation, exact path) e.g., only this op on this path.\n"
                            "  path - per (exact path), applies to all ops on that path.\n"
                            "Use 'path' if you enter a large count (e.g., 1000) and expect no more prompts on that exact path."
                        ))
    parser.add_argument('--capacity', type=int, default=DEFAULT_CAPACITY, help='Capacity in bytes (default 10MB)')
    parser.add_argument('-v', '--verbose', action='count', default=0, help='Increase verbosity')
    parser.epilog = (
        "Examples:\n"
        "  canaryfs --mount ./canary --ask -v\n"
        "  canaryfs --mount ./canary --ask --ask-scope path\n\n"
    "During a prompt, type '1000' to allow the next 1000 times for the selected scope.\n"
        "Type '10s' to allow for 10 seconds."
    )
    args = parser.parse_args(argv)

    log_level = logging.WARNING
    if args.verbose == 1:
        log_level = logging.INFO
    elif args.verbose >= 2:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format='%(asctime)s %(levelname)s %(message)s')

    mountpoint = os.path.abspath(args.mount)
    ensure_mountpoint(mountpoint)

    fs = CanaryFS(capacity=args.capacity, ask=args.ask, ask_scope=args.ask_scope)

    stop = {'flag': False}

    def handle_sigint(signum, frame):
        stop['flag'] = True
        # Trigger unmount attempt; FUSE should exit due to SIGINT
        # We'll also try a best-effort unmount after FUSE returns.

    signal.signal(signal.SIGINT, handle_sigint)

    # Foreground, single-threaded to make prompts reliable and blocking
    try:
        FUSE(fs, mountpoint, foreground=True, nothreads=True)
    except KeyboardInterrupt:
        pass
    finally:
        # Best-effort unmount
        unmount(mountpoint)

    return 0


if __name__ == '__main__':
    sys.exit(main())
