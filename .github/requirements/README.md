# Python dependency locks

The `.in` files declare direct dependency policy. Their matching `.txt` files
pin the complete graph and SHA-256 hashes consumed by CI. CI installs only from
the locks with `--require-hashes` and `--only-binary=:all:`.

Use Python from `.python-version` and install the compiler environment from
`lock-tools.txt`. Then run:

```powershell
# Verify the existing solution without selecting newer releases.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python <python> -Check

# Intentionally resolve permitted dependency upgrades.
tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python <python> -Upgrade
```

The check path seeds each temporary output from the committed lock. A newly
published package therefore cannot make an unchanged commit fail. `-Upgrade`
is the only mode that passes `--upgrade` to pip-tools.

Dependabot intentionally ignores `pip-tools`. The compiler version is a
synchronized policy tuple: `.github/requirements/lock-tools.in`, its generated
lock, the exact-version assertion in `update-python-locks.ps1`, and the semantic
guard in `tools/repo_hygiene/test_repo_hygiene.py`. Upgrade that tuple manually
and review the resulting lock-format and dependency changes together.
