# Project notes for viatom-o2ring-daemon

## Related repos to watch

- **viatom-o2ring-ble** -- https://github.com/bonelifer/viatom-o2ring-ble --
  this daemon's own BLE protocol library, pulled as a `git+https` dependency
  in `pyproject.toml` (not a versioned PyPI release). A fix or feature added
  there doesn't reach this daemon automatically: it needs
  `pip install --upgrade` (or a fresh `docker build`, which always re-clones
  at build time) to pick it up. See that repo's own `CLAUDE.md` for the five
  upstream sources *it* tracks -- a protocol fix there flows through this
  one too, eventually. That library is also still unverified against real
  hardware as of this writing, which this daemon inherits.

- **etekcity-bp-daemon** -- https://github.com/bonelifer/etekcity-bp-daemon
  -- the closest architectural sibling: also a continuous-BLE-monitoring
  device daemon (as opposed to trividia-truemetrix-daemon's docked/batch
  USB HID sync pattern), so this project's config/storage/cli/alerting/api
  shape was deliberately mirrored from it. Diverges in two deliberate ways
  worth knowing about if comparing the two: (1) this daemon has no
  multi-person "who was this?" tagging system (ntfy/dunstify, `[profiles]`
  + `[profile.<name>]`) -- a ring has exactly one wearer at a time and a
  daemon instance already targets exactly one device, so there's just one
  optional `[profile]` section instead; (2) this daemon adds a
  `sync_files.py` module and `viatom-o2ring-sync-files` console script with
  no BP-daemon analog, for downloading stored `.vld` session files from the
  ring's onboard memory -- a capability BP monitors don't have.

- **etekcity-scale-daemon** -- https://github.com/bonelifer/etekcity-scale-daemon
  -- the original architecture template the whole daemon family
  (`etekcity-bp-daemon`, `trividia-truemetrix-daemon`, and this one) was
  deliberately modeled on. Not a code dependency, just a design reference.

## Verification status

**Work in progress -- not yet verified against real hardware**, inherited
from `viatom-o2ring-ble`'s own unverified status (see that repo's
`CLAUDE.md`). Unit tests cover config/storage/report/alerting/api/prune/cli
logic against fixture data and a mocked BLE client (`sync_files.py`'s
tests), and the Docker image is CI-verified end to end (real `docker build`
+ `docker run`, not just "`pip install .` succeeds" -- see
`.github/workflows/ci.yml`), but nothing in CI can exercise the actual BLE
path -- connecting to a real O2Ring-family device, streaming live readings,
or downloading a real `.vld` file -- which hasn't been tested yet.
