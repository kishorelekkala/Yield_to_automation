# Yield_to_addition using QGIS

## Overview

This repository contains a **PyQGIS-based automation tool** that detects **yield relationships** between road lanes using **German STVO traffic rules** and geometric analysis.

The script automatically detects:

- Right-before-left priority (Rechts vor Links)
- Left-turn yield rule (STVO §9)
- Pedestrian crossing priority
- Cycle lane priority

## STVO Rules Implemented

### 1. Right-Before-Left (Rechts vor Links)

If two roads intersect:

- The vehicle coming from the right gets priority  
- The other road must yield  

---

### 2. Left-Turn Rule (STVO §9)

If two vehicles approach an intersection from opposite directions:

- The vehicle turning **left** must yield  
- The vehicle going **straight** or **right** has priority  

---

### 3. Pedestrian Priority

If a road intersects: (lane_type = pedestrian_marking)

The road must yield to pedestrians.

---

### 4. Cycle Lane Priority

If a road intersects: (lane_type = cycle_lane)


The road must yield to cycle lanes.

---

## Output Format

The script produces a layer with the following structure:

| road_id | way_id | yield_to |
|---------|--------|----------|
| 1275 | 10012 | 1354_10012,93_10012 |
| 2100 | 30011 | 4000,2200_30001 |

### Column Description

| Column | Description |
|--------|-------------|
| road_id | Main road identifier |
| way_id | Lane way identifier |
| yield_to | Comma-separated yield targets |

---

## Yield Format

Yield relationships are formatted as: road_id_way_id

Example: 1354_10012,93_10012,21

Where:

- `road_id_way_id` → road or lane to yield to  
- `road_id` → used for pedestrian crossings  

