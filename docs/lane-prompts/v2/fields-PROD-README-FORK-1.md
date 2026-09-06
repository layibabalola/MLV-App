# FIELDS for product-card-TEMPLATE.md — composed by the dispatcher; both files are in the ratified manifest
CARD_ID: PROD-README-FORK-1
PRIORITY: 14
CLIP_OR_NONE: none
ALLOWED_PATHS: README.md, CHANGELOG.md, tools/repo_hygiene/test_readme_fork_banner.py

DELIVERABLE:
`README.md` is still upstream's: shields, releases and "report bugs" point at `ilia3101/MLV-App`, and neither it nor
`CHANGELOG.md` mentions `--batch`, `--trim-mlv`, receipts, or the tested toolchain. Issues are disabled on the fork, so
the README's bug link is the only user path and it goes upstream. Add a fork banner as the FIRST section: what this
fork is (`layibabalola/MLV-App`), the headless `--batch` / `--trim-mlv` / `--receipt` surface with one example command,
the toolchain actually tested (Qt 6.10.2 / MinGW 13.1 on Windows), where to report problems for THIS fork, and that
upstream's feature list follows under "Inherited from ilia3101/MLV-App". Add a `[Unreleased]` entry to `CHANGELOG.md`
naming the headless flags. No other README content is deleted.

ACCEPTANCE:
`tools/repo_hygiene/test_readme_fork_banner.py`: asserts the first 25 lines of `README.md` contain the literal tokens
`layibabalola/MLV-App`, `--batch`, `Qt 6.10.2`, and a line starting `Report problems`; fails when any is absent (prove
once by removing one). Collected by `unittest discover`.

VERIFY_FIRST:
git -C . show {{BASE_SHA}}:README.md | head -25 | grep -c -E "layibabalola/MLV-App|--batch"     # 0 today
git -C . grep -n -e "--batch" {{BASE_SHA}} -- CHANGELOG.md     # empty today
