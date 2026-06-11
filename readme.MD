# Strava Metro → ArcGIS Online (Time-Enabled Layers)

This project processes **Strava Metro** data (edges + daily counts) and publishes a **time-enabled hosted feature layer** to ArcGIS Online using a **file geodatabase (FGDB)** workflow.

It is designed for reliable handling of **date fields**, avoiding common shapefile limitations that break ArcGIS Online time sliders.

---

## To Do

- [x] create local gdb from edge data
- [x] publish test feature class as ArcGIS Online (AGO) layer
- [ ] fix issue with date-only and datetime fields
- [ ] automate process of renaming files downloaded from Strava Metro
- [ ] automate process of saving the csv file downloaded from Strava Metro with the field name 'edgeUID' in place of the default 'edge_uid'

---

## 🚀 Overview

This script:

1. Reads:

   * Edge geometries (shapefile)
   * Strava Metro CSV (daily counts)

2. Joins:

   * One-to-many join on `edge_uid`
   * Produces one feature per edge per date

3. Writes:

   * Output to a **file geodatabase** (preserves DATE field correctly)

4. Publishes:

   * Uploads FGDB to ArcGIS Online
   * Creates/overwrites hosted feature layer
   * Enables **time slider** using a DATE field

---

## 📦 Requirements

### Software

* ArcGIS Pro (required for `arcpy`)
* Python environment: ArcGIS Pro default (`arcgispro-py3`)

### Python packages

Installed in ArcGIS Pro environment (may need a cloned environment):

* `arcgis`
* `python-dotenv`

---

## 📁 Project Structure

```
project/
│
├── strava_metro_to_ago_fgdb.py
├── .env                # (not committed)
├── .env.example       # (committed)
├── output/
└── README.md
```

---

## 🔐 Environment Variables

Create a `.env` file based on `.env.example`.

Example:

```
EDGE_SHP=C:\data\edges.shp
EDGE_CSV=C:\data\edges_counts.csv
EDGE_TITLE=Strava Metro Edges - Feb 2026
DATE_FIELD=date
OUTPUT_DIR=C:\output\rides_202602
SHARE_ORG=true
```

### Variable descriptions

| Variable     | Description                            |
| ------------ | -------------------------------------- |
| `EDGE_SHP`   | Path to Strava edge shapefile          |
| `EDGE_CSV`   | Path to CSV containing daily counts    |
| `EDGE_TITLE` | Title of hosted layer in ArcGIS Online |
| `DATE_FIELD` | Name of date field in CSV              |
| `OUTPUT_DIR` | Directory for FGDB + zip output        |
| `SHARE_ORG`  | `true/false` – share with organization |

---

## ▶️ Usage

1. download either the rides (pedalcyclists) or walk data (edges or hexagons) from the Strava Metro interface as explained in their [documentation](https://stravametro.zendesk.com/hc/en-us/articles/360051202734-Edge-Counts-Data-Export-and-Download)
2. extract the downloaded files and rename each file if desired e.g. strava_metro_edges_ride_my_county_202602401_20260430.* (this step can be automated in a future version of the script)
3. modify the necessary variables in the .env file as explained in .env.example
4. in the csv file downloaded from Strava Metro, change the name of column 'edge_uid' to 'edgeUID' so it matches the default field name in the shapefile (this step can be automated in a future version of the script)
5. from a terminal, access the ArcGIS Pro Python environment (may need to activate a virtual environment)
6. run ```python strava_metro_to_ago_fgdb.py``` (Note that the following error will most likely be thrown, but the feature layer should still be published to ArcGIS Online: ```Exception: Unable to update feature service layer definition.``` This is on the list of items to be addressed in future version of the script.)



---

## ⏱ Output

* File geodatabase created in `OUTPUT_DIR`
* Zipped FGDB uploaded to ArcGIS Online
* Hosted feature layer published
* Time slider enabled (1-day interval)

---

## 🧠 Key Design Decisions

### Why not shapefiles?

Shapefiles:

* truncate field names to 10 characters
* do not reliably support datetime
* often cause date fields to become strings

This breaks ArcGIS Online time sliders.

### Why file geodatabase?

FGDB:

* supports true `DATE` fields
* preserves schema correctly
* is the most reliable format for AGOL publishing

---

## ⚠️ Known Issues & Fixes

### 🔒 Geodatabase lock errors (`.lock` files)

You may see:

```
PermissionError: ... .gdb\*.lock
```

Fixes:

* Ensure script includes:

  ```python
  arcpy.ClearWorkspaceCache_management()
  ```
* Avoid manually opening the FGDB in ArcGIS Pro during execution
* Use unique FGDB names during testing if needed

---

### 🧪 Date field published as string

If you see:

```
Field 'date' published as esriFieldTypeString
```

Cause:

* Date not properly converted in FGDB table

Fix:

* Ensure script creates and calculates a true `DATE` field:

  ```python
  arcpy.management.AddField(..., "DATE")
  ```

---

## 📊 Data Notes

* Each edge is duplicated per day → large datasets expected
* Example: ~251k edges × 28 days = ~7M features
* Publishing time may increase with dataset size

---

## 🔄 Overwrite Behavior

If a layer with the same `EDGE_TITLE` exists:

* It will be **overwritten**

Otherwise:

* A new layer is published

---

## 🔐 Data Sharing

* Layer is shared to **organization only**
* Public sharing is disabled (Strava Metro terms)

---

## 🛠 Future Enhancements

* Add hexagon support
* Support incremental updates (append instead of overwrite)
* Add batching for very large datasets
* Add logging

---

## 📄 License / Data Use

Strava Metro data is subject to licensing restrictions.
Ensure compliance with Strava’s terms of use. Among other items, sharing the data beyond one's own organization is prohibited.

---

## 👤 Author Notes

Built for municipal GIS workflows using ArcGIS Online and Strava Metro data.

---
