# Release source identity

The Windows and Linux release workflows generate `build_buildinfo.h` in the
qmake output directory before compilation. `tools/release/build_stamp.py`
requires a clean checkout and a full HEAD equal to the workflow's source SHA.
Git failures or dirty/mismatched source stop generation without replacing an
existing header. The existing application code includes the generated header;
no fallback `unknown` stamp is accepted for these release workflows.

After compilation and again at the packaging boundary, the helper requires one
complete, null-terminated (or end-of-file) `MLVAPP_BUILDSTAMP_v1` marker containing
the expected SHA and `dirty=0`. Missing, malformed, additional, dirty or wrong-SHA
markers fail the release. Verification reports the inspected binary's size,
SHA-256 and embedded identity. It does not execute the binary or certify runtime
behavior, redistribution, or reproducible builds. Linux inspects the AppDir binary
produced by linuxdeploy; it does not independently unpack the AppImage.

This closes the observed hosted-build gap where the package inventory was valid
but the application carried `sha=unknown`. Earlier artifacts retain that finding;
only newly generated and verified artifacts can claim the embedded source SHA.

Validate the CLI behavior with
`python -m pytest tools/repo_hygiene/test_build_stamp.py`; both hosted release
workflows must also pass at the reviewed candidate commit.
