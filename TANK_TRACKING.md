# Tracking tank levels (DIY helpers)

The integration deliberately does **not** model tank capacity or percentage:
tank sizes vary, some tanks are refilled part-way, and not every install has
auto-feeders at all. The controller does not report tank levels locally.

What the integration *does* provide is the dose truth a DIY tank helper
needs:

- each chemical's **"dosed since last reset"** counter (disabled by
  default) — a count-up total in the chemical's dosing unit, persisted
  across restarts, deduplicated by dose UUID so repeated controller
  documents never double-count, and resettable with its paired button
- each counter's `last_reset` attribute (timestamp of the most recent
  reset), so a tank refill can be paired with the accounting period
- dose-only updates: the counter moves only when a *new* dose document
  arrives, never on controller re-pushes

## The pattern

1. Create one number helper per tank (Settings → Devices & Services →
   Helpers → Number). Example: "Acid tank", unit oz.
2. After a refill (full or partial), set the helper to what you actually
   filled to — e.g. 254 oz — then press the chemical's reset button once.
   The counter now reads 0 and the helper holds the tank content.
3. An automation decrements the helper each time the counter rises:

```yaml
automation:
  - alias: "Acid tank: consume doses"
    triggers:
      - trigger: state
        entity_id: sensor.pool_acid_dose_total
    conditions:
      # Doses raise the counter (positive delta); a reset lowers it and
      # must not change the tank helper (you already set it at refill).
      - condition: numeric_state
        entity_id: sensor.pool_acid_dose_total
        above: -1
      - condition: template
        value_template: >
          {{ (trigger.to_state.state | float(0)) >
             (trigger.from_state.state | float(0)) }}
    actions:
      - action: input_number.set_value
        target:
          entity_id: input_number.acid_tank
        data:
          value: >
            {{ states('input_number.acid_tank') | float(0) -
               ((trigger.to_state.state | float(0)) -
                (trigger.from_state.state | float(0))) }}
```

4. A second automation alerts at your low threshold:

```yaml
automation:
  - alias: "Acid tank: low"
    triggers:
      - trigger: numeric_state
        entity_id: input_number.acid_tank
        below: 48  # your threshold, in the helper's unit
    actions:
      - action: notify.persistent_notification
        data:
          message: "Acid tank below threshold ({{ states('input_number.acid_tank') }} oz)."
```

## Notes and limits

- **Two equal doses in a row** (e.g. 2 oz then another 2 oz) are still
  two trigger events: the *counter* changes (2 → 4) even though the
  last-dose sensor would show the same value. That is why the automation
  watches the counter, not the last-dose sensor.
- **Doses while Home Assistant is down** are counted from the
  controller's current dose document at the restart snapshot — a single
  missed-then-recovered dose is counted, but only the *last* dose per
  chemical is retained by the controller, so multiple doses during a long
  outage can under-count. Size your low-tank threshold with that margin.
- Manual doses initiated from the vendor app are dose documents too —
  they count automatically.
- The counter only counts what it observes since its last reset; pair
  each refill with a button reset (step 2) to keep the two in sync.