# Validation — 23 October 2026

Executed using `/usr/bin/python3` with GTK 4.14 and Libadwaita 1.5 in the
available Linux desktop session. Distribution dependencies were already present.

- All 10 core tests passed. Total RAM matched `/proc/meminfo`; available RAM
  agreed within the test's allowance for activity between samples. Disposable
  process RSS agreed with `/proc/PID/statm` within 4 MiB.
- Compared live readings with `free -h` and psutil. This machine reported
  12,393,099,264 bytes of usable RAM; available RAM varied during sampling.
  Differences in the definition of “used” are documented in the README.
- Real GTK smoke workflow passed, including settings, compact/pause, cancelled
  confirmation, an intentionally SIGTERM-ignoring disposable process, the
  separate force warning and acknowledgement, SIGKILL, measured results and
  reopening cleanup. No existing applications were selected or terminated.
- Graceful SIGTERM was separately verified with a disposable process. Actual
  protected PIDs and mismatched identities were refused; disappearing
  processes were handled. Permission-denial and missing-pidfd error paths were
  checked by injecting those failures, without changing system permissions.
- Dashboard was visually inspected; `dashboard.png` captures the real window.
- User installer and installed symlink launcher worked. Desktop entry passed
  `desktop-file-validate`. Debian package built and its metadata was inspected;
  system-wide package installation was not performed.
- Reusing process rows reduced an eight-second CPU sample to about 8.5% of one
  core, with 178 MiB RSS in this desktop environment, before the final additional
  incremental-row optimization. Resource use depends on GTK renderer and process
  count; this was a short sample, not an hours-long soak test.
- Tray support is conditional on a StatusNotifier watcher. No compatible tray
  host was available for interactive tray verification in this session.

Launch after user installation: `~/.local/bin/rampilot`.
