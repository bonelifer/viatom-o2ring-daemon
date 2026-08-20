# Project notes for viatom-o2ring-daemon

## Related repos to watch

- **viatom-o2ring-ble** -- https://github.com/home-health-hub/viatom-o2ring-ble --
  this daemon's own BLE protocol library, pulled as a `git+https` dependency
  in `pyproject.toml` (not a versioned PyPI release). A fix or feature added
  there doesn't reach this daemon automatically: it needs
  `pip install --upgrade` to pick it up. See that repo's own `CLAUDE.md` for the five
  upstream sources *it* tracks -- a protocol fix there flows through this
  one too, eventually. That library is also still unverified against real
  hardware as of this writing, which this daemon inherits.

  As of this writing, that library also implements a second, completely
  separate protocol -- OxyII, for the O2Ring-S (T8520) -- via a parallel
  `OxyIIClient`/`discover_oxyii()`/`OxyIIReading`/`OxyIIFileHeader`/
  `OxyIIFileRecord` (ported from nglessner/o2ring-s-protocol; see that
  library's `CLAUDE.md`). This daemon wires it up behind
  `monitor.protocol = oxyii` (default `legacy`) -- see cli.py's
  `_reading_to_row` (branches on `isinstance(reading, OxyIIReading)`) and
  sync_files.py's `_sync_oxyii_files`/`_adapt_oxyii_session` (adapts
  OxyIIFileHeader/OxyIIFileRecord, which have no embedded timestamp/
  duration/mode, into the same shape storage.record_session() already
  expects from the legacy protocol's VldHeader/VldRecord). storage.py's
  schema itself needed no changes -- `battery_state`/`perfusion_index`
  (which OxyII readings don't have) were already nullable, and
  `mode`/`percent_below_90pct`/`steps` (which OxyIIFileHeader doesn't
  have) already tolerated NULL from legacy sessions missing a field too.

- **etekcity-bp-daemon** -- https://github.com/home-health-hub/etekcity-bp-daemon
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

- **etekcity-scale-daemon** -- https://github.com/home-health-hub/etekcity-scale-daemon
  -- the original architecture template the whole daemon family
  (`etekcity-bp-daemon`, `trividia-truemetrix-daemon`, and this one) was
  deliberately modeled on. Not a code dependency, just a design reference.

## Deliberately not implemented

- **Goal-progress/trend report section** (an SpO2-floor analog to
  `etekcity-bp-daemon`'s `include_goal_progress`: current average vs. a
  doctor-set target, with a trending-toward/away-from-goal line based on a
  linear fit across the report's date range) -- considered and rejected,
  not just unbuilt. BP's version works because a sustained multi-week drop
  in average systolic/diastolic is a genuine treatment-response signal
  (weight loss, meds, diet adherence). A week-to-week SpO2 average doesn't
  have that property -- it's dominated by sensor positioning and
  night-to-night noise rather than something a person is "working toward,"
  so a linear trend line would risk implying an improving/worsening
  narrative that isn't physiologically real. `alerting.low_spo2_percent`
  already covers the actionable case (a specific reading crossed the
  floor) more honestly than a smoothed average-vs-goal chart would. If
  this comes up again, a stripped-down "current period avg vs. floor" with
  no trend line would be the fallback worth considering -- the trend-fit
  part specifically is the rejected piece, not the goal-display concept
  entirely.

## Verification status

**Work in progress -- not yet verified against real hardware**, inherited
from `viatom-o2ring-ble`'s own unverified status (see that repo's
`CLAUDE.md`). Unit tests cover config/storage/report/alerting/api/prune/cli
logic against fixture data and a mocked BLE client (`sync_files.py`'s
tests), and CI runs a real smoke test against the installed package (see
`.github/workflows/ci.yml`), but nothing in CI can exercise the actual BLE
path -- connecting to a real O2Ring-family device, streaming live readings,
or downloading a real `.vld` file -- which hasn't been tested yet.

`protocol = oxyii` (O2Ring-S) support is a partial exception, same
caveat-shape as `viatom-o2ring-ble`'s own: the underlying `OxyIIClient` is
ported from a source that verified its protocol implementation against a
real T8520, a meaningfully stronger starting point -- but the daemon-side
wiring (`_reading_to_row`'s OxyII branch, `_sync_oxyii_files`/
`_adapt_oxyii_session`) is covered by unit tests against fake clients only,
not a real device.
