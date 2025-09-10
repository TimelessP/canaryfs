# CanaryFS

CanaryFS is a 10MB in-memory FUSE filesystem for Linux that logs filesystem accesses and can interactively prompt to allow/deny each operation.

Highlights:
- Userspace only (no root), powered by fusepy.
- Volatile storage: data is lost on unmount.
- Logs all ops to stdout/stderr.
- Optional interactive prompts with time-based (e.g. `10s`) or count-based (e.g. `10`) temporary allow rules. Default single allowance with `Y` or deny with `n`.
 - Optional interactive prompts with time-based (e.g. `10s`) or count-based (e.g. `10`) temporary allow rules. Default single allowance with `Y`, deny with `n`, or `a` to allow all operations for the rest of the session.

Important: This is not ext4. FUSE filesystems implement VFS operations directly and do not use a block device or on-disk format. You just mount to a directory. No formatting required.

## Install

Requires: `fuse` (or `fuse3`) installed; your user in the `fuse` group; Linux Mint/Ubuntu/Debian packages typically `fuse3`, `fuse`, `libfuse3-3`.

Using uv (recommended):
# CanaryFS

CanaryFS is a 10MB in-memory FUSE filesystem for Linux that logs filesystem accesses and can interactively prompt to allow/deny each operation.

Highlights:
- Userspace only (no root), powered by fusepy.
- Volatile storage: data is lost on unmount.
- Logs all ops to stdout/stderr.
- Interactive prompts with:
	- `Y` or Enter: allow once
	- `n`: deny
	- `10s`: allow for 10 seconds
	- `1000`: allow next N times for the selected scope
	- `a`: allow all operations for the rest of the session (no further prompts; still logs)

Important: This is not ext4. FUSE filesystems implement VFS operations directly and do not use a block device or on-disk format. You just mount to a directory. No formatting required.

## Install

Requires FUSE and permissions:
- Packages (Mint/Ubuntu/Debian): `fuse3` (and/or `fuse`), `libfuse3-3`.
- Add your user to the `fuse` group and re-login: `sudo usermod -aG fuse $USER`

With uv (recommended):

```bash
uv pip install -e .
```

Install directly from Git (uv):

```bash
# Latest main
uv pip install git+https://github.com/TimelessP/canaryfs.git

# Specific tag or commit
uv pip install git+https://github.com/TimelessP/canaryfs.git@v0.1.0
uv pip install git+https://github.com/TimelessP/canaryfs.git@<commit-sha>
```

### Upgrade or uninstall

Using uv (recommended):

```bash
# Upgrade a Git install to the latest main
uv pip install --upgrade git+https://github.com/TimelessP/canaryfs.git

# Or upgrade to a specific tag/commit
uv pip install --upgrade git+https://github.com/TimelessP/canaryfs.git@v0.1.0
uv pip install --upgrade git+https://github.com/TimelessP/canaryfs.git@<commit-sha>

# Uninstall
uv pip uninstall canaryfs
```

Editable/local install:

```bash
# From the project root, upgrade your editable install after pulling changes
uv pip install --upgrade -e .

# If using pip directly
pip install --upgrade -e .
pip uninstall canaryfs
```

Using the helper script:

```bash
./prepare.sh
# then activate when needed
source ./.venv/bin/activate
```

Or pip manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

## Usage

Quick start:

```bash
mkdir -p ./canary
canaryfs --mount ./canary --ask -v
```

Prompt responses:
- `Y` or empty: allow once
- `n`: deny
- `10s`: allow for 10 seconds
- `1000`: allow next 1000 times for the selected scope (see below)
- `a`: allow all operations from now on (globally, this session)

Allowance scope (`--ask-scope`):
- `op` (default): allowance is for the specific operation and exact path (e.g., only `read` on `/file`).
- `path`: allowance is for the exact path across all operations (e.g., `open`/`read`/`write` on `/file`).

Notes on paths:
- Paths are exact (inside the mount). No wildcards or subtree matching.
- Renaming a file creates a new path; existing allowances may no longer apply.

Examples:

```bash
# Per-op allowances (default)
canaryfs --mount ./mnt --ask -v
# When prompted for read /mnt/file, enter 1000 to allow 1000 reads on that exact path

# Per-path allowances across all ops
canaryfs --mount ./mnt --ask --ask-scope path -v
# Enter 1000 to allow any operation on that exact path 1000 times

# Disable prompts (log-only)
canaryfs --mount ./mnt --no-ask -v
```

Unmounting:
- Press Ctrl-C in the foreground to exit; CanaryFS attempts to unmount.
- If needed, manually unmount: `fusermount3 -u ./mnt` (or `fusermount -u ./mnt`).

## Scripts

- `prepare.sh` — sets up `.venv`, upgrades pip, installs the package (uses uv if available).
- `run.sh` — simple demo runner: creates `./canary` and mounts with `--ask`.

Git ignores:
- `.gitignore` excludes local venv, build artifacts, IDE files, and common mount dirs (`./mnt`, `./canary`).

## Troubleshooting

- `fuse: failed to open /dev/fuse: Permission denied`
	- Ensure FUSE is installed and your user is in the `fuse` group, then log out/in.
- `ModuleNotFoundError: No module named 'fuse'` (from Python)
	- Install `fusepy` by (re)running install steps; it’s a declared dependency.
- `Device or resource busy` when unmounting
	- Close shells/processes in the mount dir and `fusermount3 -u ./mnt`.

## Limitations

- 10MB in-memory capacity (configurable via `--capacity`). Data is lost on unmount.
- Single-threaded foreground server to make interactive prompts reliable.
- Exact-path allowances only; no subtree/wildcard rules (possible future enhancement).
