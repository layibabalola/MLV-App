# Support

MLV-App is maintained on a best-effort basis. The project does not promise a
response time, resolution time, service level agreement, or support for every
camera, operating system, GPU, clip, or historical revision.

## Getting help

Before opening a report, search the existing documentation and issues. Useful
starting points are the [user guide](docs/01-user-guide.md),
[developer guide](docs/02-developer-guide.md), and
[test-fixture guide](docs/15-test-fixtures.md).

Issues are disabled on this `layibabalola/MLV-App` fork. For a reproducible
non-security defect or feature request, use the
[enabled upstream issue tracker](https://github.com/ilia3101/MLV-App/issues)
and include:

- the exact MLV-App commit or release and operating system;
- relevant camera, clip, receipt, and CPU/GPU details;
- concise reproduction steps and expected versus observed behavior;
- logs, screenshots, or a minimal sample when they can be shared safely; and
- whether the issue occurs on the CPU path, CUDA path, export path, or more than
  one path.

Do not upload private or sensitive footage unless you intentionally choose to
make it public. Maintainers may ask for more evidence, close reports that cannot
be reproduced, or prioritize work according to impact and available capacity.

## Security reports

Suspected vulnerabilities do not belong in public issues. Use the private route
in [SECURITY.md](SECURITY.md).

## Version scope

Security support follows [SECURITY.md](SECURITY.md). General troubleshooting is
best-effort and normally focuses on current `master`; older revisions may need
to reproduce on the current code before they can be investigated.
