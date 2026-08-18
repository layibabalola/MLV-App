# Python dependency locks

The `.in` files declare direct dependency policy. Their matching `.txt` files
pin the complete graph and SHA-256 hashes consumed by CI. CI installs only from
the locks with `--require-hashes` and `--only-binary=:all:`.

Use Python from `.python-version` and install the compiler environment from
`lock-tools.txt`. With no interpreter option, the updater verifies ambient
`python`; on Windows, if that is not the exact pinned patch, it also tries the
`py` launcher selector for the pinned major/minor. Every candidate must report
the complete version in `.python-version`, so resolution fails closed when the
exact interpreter is unavailable. Hosted `setup-python` jobs therefore use the
ambient interpreter directly, while a developer with an older ambient Python
can use the installed Windows launcher automatically.

An explicit executable or launcher is authoritative and is never silently
replaced by another candidate. Prefix launcher arguments are supported:

```powershell
# Auto-resolve the exact pinned interpreter.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Check

# Verify the existing solution without selecting newer releases.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python C:\path\to\python.exe -Check

# Or select an exact Python line through a launcher.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python py -PythonArguments '-3.13' -Check

# Intentionally resolve permitted dependency upgrades.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Upgrade
```

The check path seeds each temporary output from the committed lock. A newly
published package therefore cannot make an unchanged commit fail. `-Upgrade`
is the only mode that passes `--upgrade` to pip-tools.

Dependabot intentionally ignores `pip-tools`. The compiler version is a
synchronized policy tuple: `.github/requirements/lock-tools.in`, its generated
lock, the exact-version assertion in `update-python-locks.ps1`, and the semantic
guard in `tools/repo_hygiene/test_repo_hygiene.py`. Upgrade that tuple manually
and review the resulting lock-format and dependency changes together.
