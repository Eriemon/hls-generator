# Board Validation Scaffolds

These files are validation-only assets for remote board acceptance.

- They are not part of the generated HLS artifact surface.
- `scripts/python/remote/remote_vitis_acceptance.py --mode board` consumes them after HLS artifacts are already generated.
- Templates stay generic and parameterized so the skill does not hardcode server-specific deployment details.
