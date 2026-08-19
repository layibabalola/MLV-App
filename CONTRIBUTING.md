# Contributing to MLV-App

Thank you for improving MLV-App. Changes should be reviewable, reproducible,
and backed by evidence that matches their risk.

## Development workflow

1. Start from an up-to-date `master` and create a short-lived topic branch.
2. Keep each change focused and include tests or a durable prevention check for
   defects and workflow gaps when practical.
3. Open a pull request targeting `master`. Describe the user-visible purpose,
   validation performed, residual risks, and any hardware evidence still
   required.
4. Resolve review comments and keep the branch current. Do not merge with a
   failing required check or an unresolved product-evidence blocker.

The protected branch currently requires exactly these hosted checks:

- `Repo Hygiene Python (windows-latest)`
- `Repo Hygiene Python (ubuntu-latest)`
- `Factory Bridge Regressions`
- `Windows GUI Pilot`
- `Windows Product Oracles`

`Windows Product Oracles` runs independently so factory-control failures cannot
hide product evidence. It is a branch-protection required check: a failing
product oracle blocks merge even when every factory-control check is green.
Required hardware proof remains an additional blocker when the hosted runner
cannot exercise the applicable product path.

This repository does not require a second approver for every pull request.
Changes should still receive review proportional to risk, without creating a
review rule that prevents a solo maintainer from making progress.

## Build and test

Follow the platform setup in [the developer guide](docs/02-developer-guide.md).
On Windows, never launch Qt-linked test executables directly. Use the repository
wrapper so Qt, MinGW, and plugin paths are prepared and loader popups are
suppressed:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\testing\run-windows-test.ps1 -Suite console -TestArgs '--gtest_filter=PlaybackQualityAutoSampler.*'
pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File tools\testing\run-windows-test.ps1 -Suite pipeline -TestArgs '--gtest_filter=DualIsoPipeline.Phase4Bv3_AggressivePreviewAllowsHqMean23PreReconX4'
```

Run relevant focused tests while iterating, then the full affected suite. A
source, UI, receipt, playback, color, scaling, or processing change intended for
the Windows GUI also requires rebuilding and verifying the user-facing release
tree as described in `AGENTS.md`.

## Product oracles and output changes

Golden hashes are evidence, not a substitute for independently known-good
output. Do not regenerate a golden simply to make a candidate pass. An
intentional golden change needs a stated reason and reviewed before/after
evidence; dirty or unstamped executables are not valid blessing sources.

Changes described as behavior-preserving, byte-identical, refactors,
performance work, scheduling work, proxies, or no-ops are the highest-risk
output class. They require a controlled baseline A/B against the pinned
known-good build on the named real-clip corpus in `AGENTS.md` and
[the regression-prevention program](docs/regression-prevention-program.md).
Each leg must independently prove advancing presented frames. The blocking
color and exposure legs use the real 8-bit present path; pixels and cadence do
not override a failing blocking leg.

CUDA playback and CUDA-output claims must be proven on the CUDA-capable host
named **Bachelor**, with the baseline and candidate commits, binaries, clip,
receipt, frame/history identity, and evidence bundle pinned. CPU or factory
diagnostics from this VM do not prove CUDA behavior. If Bachelor is unavailable
or the evidence cannot be bound to the exact candidate, report the claim as
blocked; do not infer a pass from a local or same-codebase proxy.

## Python dependency locks

Use the exact Python version in `.python-version`. The `.in` files declare
direct policy and the generated `.txt` files pin the complete dependency graph
with hashes. The following Windows PowerShell bootstrap is self-contained: it
verifies Python 3.13.15, creates a fresh compiler environment, installs only
hash-locked wheels from the committed bootstrap and lock-tools locks, checks the
environment, and then verifies the generated locks.

```powershell
$expectedPython = '3.13.15'
$resolvedPython = py -3.13 -c "import sys; print(sys.executable)"
if ((& $resolvedPython -c "import platform; print(platform.python_version())") -ne $expectedPython) { throw "Python $expectedPython is required" }
$lockToolsVenv = Join-Path ([System.IO.Path]::GetTempPath()) ("mlvapp-lock-tools-{0}" -f [guid]::NewGuid().ToString('N'))
& $resolvedPython -m venv $lockToolsVenv
$lockPython = (Resolve-Path (Join-Path $lockToolsVenv 'Scripts\python.exe')).Path
& $lockPython -m pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r .github\requirements\pip.txt
& $lockPython -m pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r .github\requirements\lock-tools.txt
& $lockPython -m pip check
& tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python $lockPython -Check
```

Use the same bootstrapped `$lockPython` with `-Upgrade` only when intentionally
resolving permitted dependency updates:

```powershell
& tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python $lockPython -Upgrade
& tools\dependencies\update-python-locks.ps1 -RepoRoot . -Python $lockPython -Check
```

Review generated lock changes and keep hashes. See
[the lock policy](.github/requirements/README.md) for the compiler-version
tuple and Dependabot boundary.

## Vendored release payloads

Archive integrity and redistribution readiness are separate gates. Validate
tracked native payload bytes and structure with the exact Python 3.13.15 from
`.python-version`. On Windows:

```powershell
if ((py -3.13 -c "import platform; print(platform.python_version())") -ne '3.13.15') { throw 'Python 3.13.15 is required' }
py -3.13 -m tools.repo_hygiene.vendored_native_payloads --repo-root .
py -3.13 -m tools.repo_hygiene.vendored_native_payloads --repo-root . --require-redistribution-ready
```

On Linux or macOS, select an explicit 3.13.15 interpreter rather than ambient
`python` or `python3`:

```sh
PYTHON_31315=/absolute/path/to/python3.13
test "$("$PYTHON_31315" -c 'import platform; print(platform.python_version())')" = "3.13.15"
"$PYTHON_31315" -m tools.repo_hygiene.vendored_native_payloads --repo-root .
"$PYTHON_31315" -m tools.repo_hygiene.vendored_native_payloads --repo-root . --require-redistribution-ready
```

The committed manifest at `tools/gates/vendored-native-payloads.json` currently
marks redistribution readiness as `blocked`. The first command may pass while
the strict command remains red. Do not publish a redistributable release until
the strict command passes with verified sources, build recipes, architecture,
licenses, notices, and extraction coverage. Never describe integrity-only
success as release readiness.

## Security

Do not report suspected vulnerabilities in a public issue or pull request.
Follow [SECURITY.md](SECURITY.md), which provides the private vulnerability
reporting route and the supported-version policy.
