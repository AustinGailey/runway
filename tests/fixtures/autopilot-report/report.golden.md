# Autopilot run report: Painter site

Run `autopilot-site-1791000000` in `/tmp/painter-site`. Stopped: **dollar cap reached**. t-4 would cost about $0.4200 with $0.3110 left under the $1.00 cap (spent $0.6890).

Started 2026-10-03 04:00 UTC, ended 2026-10-03 05:00 UTC: 60.0 min wall-clock.

## Summary

| Attempted | Merged | Blocked | In review | Left to do |
| ---: | ---: | ---: | ---: | ---: |
| 3 | 1 | 1 | 1 | 2 |

Spend $0.689 of the $1.00 cap (68.9%), $0.311 left.

## Tasks

| Task | Outcome | Est. tokens | Actual tokens | Actual ÷ est. | Model | Cost |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| t-1: Hero section | merged | 200,000 | 231,500 | ×1.16 | claude-sonnet-5 | $0.412 |
| t-2: Pricing \| table | blocked (red) | 150,000 | 98,200 | ×0.65 | claude-sonnet-5 | $0.211 |
| t-3: Contact form | review | 120,000 | 42,000 | ×0.35 | claude-haiku-4-5-20251001 | $0.066 |
| **Total** | | 470,000 | 371,700 | | | $0.689 |

## Time (merge train buckets)

| Machine min | Git and merge min | Setup min | Test min | Merge min | Retries | Conflicts resolved | Human touches |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 14.32 | 1.87 | 0.41 | 12.45 | 1.46 | 1 | 0 | 0 |

Review and client minutes are not automated, so they are not counted here.

## Needs a person

- The dollar cap stopped the run: t-4 would cost about $0.4200 with $0.3110 left under the $1.00 cap (spent $0.6890).
- **t-2:** Blocked: Checks failed: npm test exited 1.
- **t-3:** In review and not merged: run the autopilot again to merge it.
- Not started (2): t-4, t-5

## Reconciliation

Budget ledger: 4 rows, $0.689, 371,700 effective tokens. The tasks account for $0.689; unattributed $0.000. Reconciled.
