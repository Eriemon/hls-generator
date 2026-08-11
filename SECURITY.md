# Security Policy

## Supported versions

Security fixes are tracked for the newest published skill version. The current supported release is `0.5.0`.

Older release directories are immutable history. If an older release is still deployed, upgrade from its matching `dist/readable-hls-generator-vX.Y.Z/` directory to the newest validated release before reporting a security issue that may already be fixed.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Contact the maintainers at [<REDACTED_EMAIL>](mailto:<REDACTED_EMAIL>) with:

- the affected release and the exact command or workflow stage;
- a minimal reproduction that does not contain credentials, private source, or board secrets;
- the observed impact and whether the issue affects generated artifacts, local configuration, remote validation, or release packaging.

The skill does not store server passwords, private keys, hostnames, or board-specific defaults in its public package. Remote server selection and credentials remain in user-local configuration handled by the configured remote workflow.

## Scope boundary

This repository governs readable AMD/Xilinx Vitis HLS workflows. Handwritten Verilog/SystemVerilog, arbitrary host automation, and unrelated hardware-tool security issues are outside the skill's implementation scope; explain the HLS trace when a report crosses that boundary.
