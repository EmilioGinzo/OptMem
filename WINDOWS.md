# Windows and Linux

OptMem uses Python 3 and its standard library; no WSL or external Python package
is required on Windows. Use Python 3.8 or newer. The `install.sh` installer is
for a POSIX shell on Linux/macOS; use the manual Windows setup below instead.

## Windows setup (PowerShell)

From a checkout of this fork's `feature/selective-retention` branch:

```powershell
# Choose a store BEFORE init; this example uses a temporary directory.
$env:MEMORY_DIR = Join-Path $env:TEMP 'optmem-example-memory'
python ./memo init
python ./memo wake --current
```

Use a disposable example directory when experimenting. For normal use, choose
the desired persistent store or omit `MEMORY_DIR` to use
`$env:USERPROFILE/.optmem/memory`. Keep private memory outside a public checkout.
The tool file may be copied anywhere; preserve it as UTF-8. Paste the printed
Memory block into the agent's instructions to adopt selective retention.

Printed follow-up commands use PowerShell's `&` operator and explicit quoted
Python/script paths. They work when paths contain spaces, apostrophes, dollar
signs or brackets and do not require the tool or Python directory on PATH.
If using Command Prompt instead, invoke `python path/to/memo` manually; do not
paste the PowerShell `&` commands into `cmd.exe`.

Candidate files are UTF-8 JSON. Both plain UTF-8 and the BOM produced by Windows
PowerShell 5's `Set-Content -Encoding UTF8` are supported. Log records are always
UTF-8 bytes of fixed width, independent of console encoding or OS line endings.

## Linux setup

Use the README's `install.sh` command or run `python3 ./memo init` from the
checkout after selecting an appropriate `MEMORY_DIR`. Generated commands are
quoted for a POSIX shell. Git attributes keep `memo`, shell scripts and Python
tests in LF format even after Windows checkouts. `memo` remains executable in Git.

## Locking and validation

Linux uses `fcntl.flock`; native Windows uses `msvcrt` advisory locking. The
read/compare/append transaction for keyed updates uses the same store lock.
The lock file opens in append mode, avoiding accidental truncation. This is
same-machine coordination; do not concurrently write a store from different
machines through a synced folder or assume network filesystems preserve locks.

```powershell
python test.py
python -m unittest -v test_retention
```

On Linux replace `python` with `python3`. All tests use temporary synthetic
stores; both `HOME` and `USERPROFILE` are isolated. Concurrency tests use real
processes for same-key creation, competing updates, and duplicate retries.
The Windows run skips only the POSIX chmod-permission test. The Linux run must
execute it as a non-root user, because root can bypass file read permissions.
