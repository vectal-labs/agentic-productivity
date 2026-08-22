# Local timezone verification — 2026-08-22

## Decision

ADR 0003 already approves the product behavior: daily boundaries follow the Mac's local timezone, while existing historical day keys remain unchanged.

## Assumptions checked

- A named IANA timezone is needed for correct historical and daylight-saving rules.
- The same detected timezone must drive event attribution, report-day selection, status output, and delivery timestamps.
- A new process may pick up a later operating-system timezone change without rewriting stored history.

DeepAPI research requests `a9102f16-d681-4db6-a73c-824ddea544ee`, `9eef6ee0-52e3-4861-93f9-11cbec1c0ddb`, and `fff980c7-7837-42cc-a9ca-4492de483ca5` supported named-zone conversion and local calendar boundaries. The implementation follows the existing ADR rather than adding a separate fixed reporting-timezone preference.

## Live checks

The detector and day conversion were run against 20 real IANA zones across America, Europe, Africa, Asia, Australia, and the Pacific.

- 20 of 20 timezone detections passed.
- 80 of 80 UTC-to-local day assignments passed.
- 28 of 80 assignments differed from the old Warsaw result, confirming the old hardcoding materially changed users' daily totals.
- Unit behavior checks passed for macOS zone discovery and cross-date attribution between Los Angeles and Auckland.
