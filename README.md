# Yield Rules Pipeline —  HD Lane Maps

Fills in the **`yield_to`** field automatically for T-junctions (3 arms) and
cross-junctions (4 arms) in HD Lane Maps, following German traffic
rules.

Select an intersection in QGIS, press Run, and the tool works out who gives
way to whom.

---

## 1. What it does

**In:** a HD Lane Map GeoJSON, with one intersection selected in QGIS.

**Out:**
- the `yield_to` field filled in on every centerline at that intersection
- yield lines drawn on the map
- a report saying which rules were used and why

**Rules it applies:**

- right before left
- stop sign (DE 206)
- yield sign (DE 205)
- priority sign (DE 306 / DE 301)
- priority path sign (DE 1002-xx — a priority road that turns)
- traffic lights (red / yellow / green, and controlled turn arrows)

---

## 2. What the data must contain

**This is the part to get right.** If an attribute is missing the tool either
skips the feature or guesses, and it tells you which in the report.

### Select all three of these at the intersection

| Select | `lane_type` | Why it is needed |
|---|---|---|
| Centerlines | `centerline` | These are the lanes that get a `yield_to` value |
| Road borders | `road` | Needed to work out which way each lane travels |
| Signs and lights | `regulatory_element` | These decide priority |

Road borders are easy to forget. **Without them the direction of travel
cannot be checked**, and a lane drawn backwards will be read backwards.

### Required attributes

**On every centerline:**

| Attribute | Must be | Used for |
|---|---|---|
| `lane_type` | `centerline` | Finding the lanes |
| `lane_morphology` | `intersection` | Confirming you are at a junction |
| `road_id` | filled | Goes into the `yield_to` value |
| `way_id` | filled | Goes into the `yield_to` value, **and sets direction of travel** |
| `traffic_rule` | the `re_id` of its sign, or empty | Linking the lane to its sign |
| geometry | a LineString through the junction | Everything |

**On every sign or light:**

| Attribute | Must be | Used for |
|---|---|---|
| `lane_type` | `regulatory_element` | Finding them |
| `line_type` | `traffic_sign` or `traffic_light` | Sign or light |
| `line_sub` | `de306`, `de205`, `de206`, `de301`, `de1002-xx` … | Which rule applies |
| `re_id` | filled, and matching a `traffic_rule` on a lane | The link to the lane |

**On every road border:**

| Attribute | Must be |
|---|---|
| `lane_type` | `road` |
| `road_id`, `way_id` | filled |

**Output field:** `yield_to` — created automatically if the layer does not
have it.

### Before you run: check lane direction is correct

Lane direction comes from `way_id`. The converter's `correct_lane_direction`
only fixes a lane when **both of its outer borders are present** in the data 
if a border is missing it quietly skips that lane and leaves it drawn
backwards.

So before running this tool:

1. Make sure `correct_lane_direction` has been run on the map.
2. Make sure no road borders are missing at the junction.

The tool checks direction again itself and warns you if a lane looks wrong,
but it cannot fix a junction whose borders are not there.

### Coordinate system

The layer may be in degrees (EPSG:4326)  the tool reprojects to metres by
itself and does **not** modify your layer.

---

## 3. How to use it

1. QGIS → **Plugins → Python Console** (Ctrl+Alt+P)
2. Click **Show Editor**
3. Open `scripts/run_in_console.py`
4. Set `PIPELINE_PATH` near the top — once only:
   ```python
   PIPELINE_PATH = r"/your/path/to/yield_to_pipeline"
   ```
5. **Select the intersection on the canvas** — drag a box round it
6. Press **Run**

Nothing to type per junction.

### The one switch

```python
DRY_RUN = True      # print everything, write nothing   <- always start here
DRY_RUN = False     # write yield_to and draw the yield lines
```

### If your selection covers two junctions

They are not mixed. Each is processed separately, so you can select
generously — or select several junctions at once.

---

## 4. How it works

```
   ┌─────────────────────────────────────────────┐
   │  You select an intersection in QGIS         │
   └───────────────────┬─────────────────────────┘
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  1   READ THE DATA                          ║
   ╚═══════════════════╤═════════════════════════╝
      Keep lane_type = centerline
      Keep lane_morphology = intersection
      Reproject to metres if needed
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  2   FIND THE ARMS                          ║
   ╚═══════════════════╤═════════════════════════╝
      Find the centre, group the lane ends
      into arms  ->  N / E / S / W
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  3   WHICH WAY DOES EACH LANE GO?           ║
   ╚═══════════════════╤═════════════════════════╝
      From way_id, checked against the road borders
                       │
      Each lane now has:  FROM arm X  ->  TO arm Y
      and a turn:  left / straight / right
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  4   WHAT CONTROLS THIS JUNCTION?           ║
   ╚═══════════════════╤═════════════════════════╝
      traffic_rule -> re_id -> line_sub
                       │
      ┌────────────────┼────────────────┐
      │                │                │
  traffic light?   306/205/206?     nothing?
      │                │                │
 SIGNALISED      SIGN CONTROLLED  RIGHT BEFORE LEFT
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  5   DO TWO LANES ACTUALLY MEET?            ║
   ╚═══════════════════╤═════════════════════════╝
      CROSS  - the paths cross
      MERGE  - they end in the same lane
      NONE   - they never meet  ->  NO YIELD, stop
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  6   WHO GIVES WAY?                         ║
   ╚═══════════════════╤═════════════════════════╝
      Only for pairs that meet. First match wins:
        1. different light phase  -> nobody yields
        2. protected green arrow  -> nobody yields
        3. higher priority sign   -> lower one yields
        4. other is on my RIGHT   -> I yield
        5. I turn left, other oncoming -> I yield
                       │
   ╔═══════════════════▼═════════════════════════╗
   ║  7   WRITE IT OUT                           ║
   ╚═══════════════════╤═════════════════════════╝
      yield_to  =  "road_id''_way_id", ...
      Draw the yield lines
                       │
   ┌───────────────────▼─────────────────────────┐
   │  Done                                       │
   └─────────────────────────────────────────────┘
```

**Step 5 happens before step 6.** A car can be on your right and you still do
not give way because your paths never meet. A right turn leaves the junction
straight away and never touches the traffic coming from the right.

That one idea removes most special cases: four cars all turning right yield to
nobody, and two opposing left turns pass each other without yielding.

---

## 5. What you get in console

The report opens by saying which rule set was chosen:

```
Control
  Type        : UNSIGNALISED, NO PRIORITY SIGNS
  Rule set    : RIGHT-BEFORE-LEFT (StVO 8)
```

then the result:

```
  yield_to
    106/20012      E->S      -> "108''_10012","110''_10012"
    110/20012      E->W      -> (empty)
    106/10012      S->E      -> "110''_10012"
```

Warnings appear when something needs checking — a missing sign, a lane whose
direction looks wrong, or two lanes each told to yield to the other.

---

## 6. Files you open

| File | What for |
|---|---|
| `scripts/run_in_console.py` | **The file you run.** Settings at the top. |
| `yield_pipeline/config.py` | Column names and sign codes, if the data changes. |

Everything else is the engine and does not need opening.

---

## 7. Settings

In `yield_pipeline/config.py`.

| Setting | Default | Change it when |
|---|---|---|
| `SchemaConfig` names | current export | The attribute table is renamed |
| `yield_sign` | `("205",)` | Only 306/301/205/206 set priority. de341 is a marking. |
| `conflict_clearance` | `1.0` m | Real conflicts are being missed on loosely drawn turns |
| `item_template` | `{road_id}''_{way_id}` | The `yield_to` format changes |
| `right_hand_traffic` | `True` | .... |

---

## 8. Status

| Part | State |
|---|---|
| Lane direction of travel | Done |
| Right before left | Done |
| Yield / stop / priority signs | Need to test the |
| Priority path sign (1002-xx) | Written, **not tested** |
| Traffic lights | Written, **not tested** |
| Yield lines | Update needed |
| Writing `yield_to` | Done |
| Cycle lanes | **Not yet** — reported and needs to add |
| Animation visualisation | **Done** — scripts/German Intersection Rules Animation.html |
| Module in the Annotation & QC tool | **Not started** — runs from the QGIS console today |


---

## 9. Requirements

- QGIS 3.x with the Python console
- No extra Python packages
