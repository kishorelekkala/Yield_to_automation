# How to use this

## 1. Which files you actually touch

Out of 15 files, **two**:

| File | What for |
|---|---|
| `yield_pipeline/config.py` | Tell it your column names and sign codes. This is the only file you edit. |
| `scripts/qgis_yield_to_algorithm.py` | The thing you load into QGIS. You don't edit it. |

Everything else is engine. You never open it unless a rule is wrong:

```
yield_pipeline/
  extract.py       reads the attribute table, finds the arms   <- Epic 1
  conflicts.py     works out which paths actually meet
  rules.py         decides who yields to whom                  <- Epics 2-5
  writer.py        formats the yield_to cell, draws yield lines <- Epic 6
  geometry.py      maths. bearings, line crossings.
  model.py         data shapes. no logic.
  qgis_adapter.py  the only file that talks to QGIS
scripts/
  demo.py          runs 4 example junctions, no QGIS needed
tests/
  test_scenarios.py  25 tests against your HTML
  fixtures.py        builds fake intersections for those tests
```

---

## 2. What the input data must have

### Required — on every centerline you select

| Column | Value | Why |
|---|---|---|
| `lane_type` | `centerline` | Stage-1 filter |
| `lane_morphology` | `intersection` | Stage-1 gate: confirms you're at a junction |
| `road_id` | e.g. `4711` | Goes into the output string |
| `way_id` | e.g. `880231` | Goes into the output string |
| `traffic_rule` | `RE_12,RE_13` | The `re_id`s of linked regulatory elements |
| geometry | LineString | Must run **entry arm → exit arm**, in travel direction |

### Required — on every regulatory element you select

| Column | Value |
|---|---|
| `lane_type` | `regulatory_element` |
| `line_type` | `traffic_sign` or `traffic_light` |
| `line_sub` | `de306` / `de205` / `de206` / `1002-10` … |
| `re_id` | `RE_12` — must match what the centerline named |

`line_sub` matching ignores case, spaces, underscores and dots. `DE 306`,
`de_306`, `de306` and `306` all work. Several codes in one cell are fine:
`de306,1002-10`.

### Output

| Column | |
|---|---|
| `yield_to` | Created automatically if the layer hasn't got it. Text, length 2000. |

### Optional — used if present, inferred if not

| Column | If missing |
|---|---|
| `direction` (`forward`/`backward`) | Assumes the geometry is drawn in travel direction |
| `turn_direction` (`left`/`straight`/`right`) | Worked out from the geometry's heading change |
| `lane_index` (0 = kerbside) | Assumes 0. **Needed for 3+ lane junctions** so merges land in the right lane |
| `signal_group` | Assumes a 2-phase plan pairing opposite arms |
| `protected` (true/false) | Assumes no protected green arrows |

### Pedestrian / cycle crossings — optional

`lane_type` or `line_type` = `crosswalk`, `pedestrian_crossing`, `zebra`,
`bicycle_crossing`, `cycle_crossing`, with a LineString across the arm.

### The absolute minimum

You can run with only `lane_type`, `lane_morphology`, `road_id`, `way_id` and
geometry. With no signs at all you get **right-before-left**, which is correct
— an unsigned junction *is* right-before-left.

---

## 3. What it does NOT need

- No bounding box or intersection-picking tool. **Your selection is the input.**
- No N/S/E/W column. Arms are worked out from the geometry's bearings.
- No manual turn labelling.
- No projected-vs-geographic worry — but the CRS **must be projected**
  (metres). Lat/long degrees will make the distance tolerances meaningless.

---

## 4. Running it — three ways

### A. In QGIS (the real thing)

1. Unzip the folder anywhere, e.g. `C:\hdmap\yield_to_pipeline\`
2. QGIS → Processing Toolbox → the Python icon → **Add Script to Toolbox…**
3. Pick `scripts/qgis_yield_to_algorithm.py`
4. It appears under **HD Map → Populate yield_to (German traffic rules)**
5. **Select one intersection's features** on the canvas — the centerlines and
   their regulatory elements
6. Run it

Parameters:

| Parameter | Default | Note |
|---|---|---|
| Centerline layer | — | Where the centerlines live |
| Regulatory-element layer | empty | Only if signs are in a *different* layer |
| Use selected features only | ✅ | Leave ticked |
| Output column | `yield_to` | |
| Conflict clearance | `1.0` | Map units. Paths closer than this count as conflicting |
| Add 'Yield lines' layer | ✅ | Red bars where each yielding car waits |
| **Report only** | ❌ | **Tick this the first few times** — logs everything, writes nothing |

### B. Without QGIS, to see what the output looks like

```bash
cd yield_to_pipeline
python3 scripts/demo.py
```

Four junctions: unsigned, DE 306/205, bent priority with the 1002-10 plate,
and signalised. Prints the arms, every assignment and every warning.

### C. To check the rules are right

```bash
python3 tests/test_scenarios.py
```

25 tests reproducing your HTML scenario set — all 3 phases of Scenario 6, all
5 of Scenario 5, the whole 12-movement `CASES_10` table, the `CASES_12`
T-junction, and Scenarios 1–4. If you change a rule, run this.

---

## 5. Adapting it to your attribute table

Open `yield_pipeline/config.py`. If your columns are named differently:

```python
@dataclass
class SchemaConfig:
    road_id: str = "ROAD_ID"            # <- your name
    way_id: str = "WAY_ID"
    lane_type: str = "LaneType"
    lane_morphology: str = "LaneMorph"
    traffic_rule: str = "TrafficRule"
    ...
    centerline_value: str = "centerline"     # <- your value
    intersection_value: str = "intersection"
```

If the output format is different from `"road_id''_way_id"`:

```python
@dataclass
class OutputConfig:
    item_template: str = "{road_id}''_{way_id}"   # change the shape
    item_separator: str = ","
    quote_items: bool = True                       # False = no "  "
```

If a sign code is written differently:

```python
@dataclass
class SignCodes:
    priority_road: tuple = ("306", "VZ306")
```

---

## 6. Reading the output

The log prints a block like this:

```
Junction summary
  centre        : (412334.20, 5801442.90)
  control       : sign / unregulated
  arms          : E(YIELD), N(YIELD), S(PRIORITY), W(PRIORITY)
  movements     : 12 (0 VRU crossings)
  yield relations: 28

yield_to assignments
  E->N       right     -> "4711''_8812","4712''_8830"
  E->W       straight  -> "4711''_8812","4713''_8841", ...
  S->N       straight  -> (none)
```

- **`arms`** — check this first. If an arm says `UNREGULATED` when it carries
  a sign, the `re_id` link is broken and the warnings will say which.
- **`(none)`** means that movement gives way to nobody. Correct for a priority
  road going straight or right.
- Every line is a centerline in your table; the string is what lands in the cell.

### Warnings worth stopping for

| Warning | What it means |
|---|---|
| `NOT at an intersection` | A selected centerline isn't tagged `intersection`. Excluded. |
| `names re_id 'X' but no regulatory_element…` | You didn't select the sign, or the link is wrong |
| `deadlock: A and B each yield to the other` | The signs on those approaches contradict each other |
| `unexpected: X (PRIORITY) yields to Y (YIELD)` | A rule produced something that can't be right — tell me |

### The audit trail

Every pair the engine considered, with the reason:

```python
for d in result.decisions:
    print(d["ego"], d["other"], d["conflict"], d["yields"], d["reason"])
```

```
S->W  N->S  cross  True   left turn yields to oncoming traffic
S->W  E->N  none   False  paths do not conflict
S->E  W->E  merge  True   sign: lower priority class yields
```

That's how you check a junction the engine got wrong — it tells you whether
the mistake was in conflict detection or in the rule.

---

## 7. First run, step by step

1. Edit `config.py` so the column names match your table.
2. Select one simple 4-way intersection with no signs.
3. Run with **Report only** ticked.
4. Check the `arms` line reads `N/E/S/W` sensibly and the turn types are right.
5. Check a few assignments by hand against the HTML.
6. Repeat on a signed junction, then a bent-priority one.
7. Untick **Report only**.

If step 4 gives nonsense arms, the geometry isn't drawn entry→exit through the
junction — that's assumption **A9** in `config.py`, and the extraction stage
needs a pairing step before anything else will work. Tell me and I'll add it.
