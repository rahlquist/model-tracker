# RANKING — model-tracker

The `rank` operation computes a usage-weighted `agent_rating` per model, writes
it back to each of that model's `user_notes` rows, and prints a report.

## Algorithm (locked — implement exactly)

Grouped by `model_info.model_name`, falling back to `model_alias` when the name
is empty. Joins: `user_notes → model_info → system_config`.

```
For each model M:
  eligible_notes = user_notes joined to M
  # completeness filter (EXCLUDE_INCOMPLETE, default True):
  #   when True, drop notes whose linked system_config.was_complete = false
  #   from `base`
  base    = avg(user_rating over eligible_notes)   # null if no notes -> unranked
  usage   = sum(nturns over all system_config rows linked to M via model_info)
  score   = base * (1 + log10(1 + usage) * USAGE_WEIGHT)
  if any linked system_config.was_complete = false: score -= PENALTY_INCOMPLETE
  if any linked system_config.was_errors is non-empty: score -= PENALTY_ERRORS
  agent_rating = clamp(score, 1.0, 10.0)
```

- `base` null (no eligible notes) ⇒ model is **unranked**; `agent_rating`
  stays `NULL` and is not written (the report shows `unranked` / `-`).
- `log10(1 + usage)` rewards total usage; with `usage = 0` the multiplier is 1,
  so a model with notes but no recorded turns still uses `base` directly.
- Penalties apply based on the **linked** `system_config`, regardless of whether
  the note was excluded from `base`.

### Tie-breaks

Equal `agent_rating` → higher total `nturns` first; still equal → most recent
`model_last_use`. Unranked models always sort last.

## Config constants

In `scripts/ranking.py` (top of file), overridable in `config.toml [ranking]`:

| key | default | meaning |
|---|---|---|
| `USAGE_WEIGHT` | `1.0` | weight of `log10(1+usage)` multiplier |
| `PENALTY_INCOMPLETE` | `2.0` | subtract when any linked run was incomplete |
| `PENALTY_ERRORS` | `1.0` | subtract when any linked run had errors |
| `EXCLUDE_INCOMPLETE` | `true` | drop incomplete-run notes from `base` |

`config.toml` example:

```toml
[ranking]
USAGE_WEIGHT = 1.0
PENALTY_INCOMPLETE = 2.0
PENALTY_ERRORS = 1.0
EXCLUDE_INCOMPLETE = true
```

## Report format

Plain-text table by default; `--markdown` for a Markdown table. Columns:
`Rank, Model, Agent, AvgUser, Turns, Sess, Inc, Err, LastUse`. `Inc`/`Err` show
`Y` when any linked run was incomplete / had errors.

## Worked example

Seed (single system, three models + one unranked):

| model | notes (user_rating) | nturns | was_complete | was_errors |
|---|---|---|---|---|
| model-a | 9, 10 | 100 | true | "" |
| model-b | 7 | 50 | **false** | "" |
| model-c | 5 | 30 | true | "timeout" |
| model-d | (none) | 10 | true | "" |

Compute (USAGE_WEIGHT=1.0, PENALTY_INCOMPLETE=2.0, PENALTY_ERRORS=1.0,
EXCLUDE_INCOMPLETE=true):

- **model-a**: base = avg(9,10) = 9.5; usage = 100;
  score = 9.5·(1+log10(101)) = 9.5·3.0043 = 28.54 → clamp **10.00**.
- **model-b**: linked run incomplete ⇒ `base` is dropped from eligible notes ⇒
  no eligible notes ⇒ **unranked** (agent_rating NULL).
- **model-c**: base = 5; usage = 30;
  score = 5·(1+log10(31)) − 1 (errors) = 5·2.4914 − 1 = 11.46 → clamp **10.00**.
- **model-d**: no notes ⇒ **unranked**.

Ordering: A(10.00, 100 turns) > C(10.00, 30 turns) > B(unranked) > D(unranked).
`agent_rating` written back to model-a's two notes (10.0) and model-c's note
(10.0); model-b and model-d stay NULL.

This is exactly what `scripts/ranking_smoke.py` produces (see acceptance test 6).
