# Remote Board Platform Upload

Use this runbook when `scripts/python/remote/remote_vitis_acceptance.py --mode board` reports that the
remote host has an active U55C shell but no matching installed platform/XPFM.

## Default Payload Shape

- Preferred input is a fully extracted local platform directory named
  `xilinx_u55c_gen3x16_xdma_3_202210_1/`.
- The directory must contain the matching `.xpfm` and its required metadata.
- Do not use `.deb` or `.rpm` as the primary path for this skill flow.

## Governed Remote Location

- Remote workspace root: `~/workspace/`
- Governed project root: `~/workspace/erie-hls-generator/`
- Governed platform root:
  `~/workspace/erie-hls-generator/platforms/alveo/xilinx_u55c_gen3x16_xdma_3_202210_1/`
- Expected XPFM path:
  `~/workspace/erie-hls-generator/platforms/alveo/xilinx_u55c_gen3x16_xdma_3_202210_1/xilinx_u55c_gen3x16_xdma_3_202210_1.xpfm`

## Upload Flow

1. Archive the local platform directory into a single tarball or zip.
2. Upload the archive with `erie-remote-ssh request-upload`.
3. Extract it on the remote host with `erie-remote-ssh request-command`.
4. Rerun board validation with explicit board platform arguments so the
   selection is written back to `~/.hls-generator/config.json`.

## Command Shape

```powershell
python <erie-remote-ssh>/scripts/remote_ssh.py request-upload --settings <erie-settings.json> --server server_6 --local <local-platform-archive> --remote erie-hls-generator/platforms/alveo/xilinx_u55c_gen3x16_xdma_3_202210_1.tar.gz --reason "upload U55C platform payload"
python <erie-remote-ssh>/scripts/remote_ssh.py request-command --settings <erie-settings.json> --server server_6 --reason "extract U55C platform payload" -- bash -lc "mkdir -p <REDACTED_LOCAL_PATH> && tar -xzf erie-hls-generator/platforms/alveo/xilinx_u55c_gen3x16_xdma_3_202210_1.tar.gz -C <REDACTED_LOCAL_PATH> --strip-components=1"
python .\scripts\python\remote\remote_vitis_acceptance.py --mode board --server server_6 --platform-name xilinx_u55c_gen3x16_xdma_3_202210_1 --remote-platform-root <REDACTED_LOCAL_PATH> --remote-xpfm <REDACTED_LOCAL_PATH> --example-spec hls_host_kernel_split_spec.json --comment-language zh --json
```

## Expected Outcome

- `scripts/python/remote/remote_vitis_acceptance.py --mode board` no longer reports
  `no_matching_platform_shell_detected`.
- `board_profile.remote_xpfm` is populated in the result JSON.
- A subsequent full `scripts/python/validation/confidence_loop.py --server server_6 ...` can reach board
  compile/link/host-run instead of stopping at platform preflight.
