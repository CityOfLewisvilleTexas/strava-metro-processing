"""
strava_metro_to_ago_fgdb.py

Creates a time-enabled ArcGIS Online hosted feature layer from
Strava Metro edge data using a file geodatabase instead of a shapefile.

Why this version:
- shapefiles often coerce datetime/date fields in ways that break AGOL timeInfo
- file geodatabases preserve real DATE fields much more reliably

Intended environment:
- ArcGIS Pro Python environment (so arcpy is available)

Dependencies:
    arcgis
    python-dotenv
    arcpy  (comes with ArcGIS Pro)
"""

import os
import json
import shutil
import zipfile
from pathlib import Path
# TODO - use either dateime or time
import datetime as dt
import time
import gc

from dotenv import load_dotenv

import arcpy
from arcgis.gis import GIS
from arcgis.features import FeatureLayerCollection


# ---------------------------------------------------------------------------
# CONFIG

EDGE_COUNT_FIELDS = [
    "total_trip_count",
    "forward_trip_count",
    "reverse_trip_count",
    "forward_people_count",
    "reverse_people_count",
    "forward_commute_trip_count",
    "reverse_commute_trip_count",
    "forward_leisure_trip_count",
    "reverse_leisure_trip_count",
    "forward_morning_trip_count",
    "reverse_morning_trip_count",
    "forward_midday_trip_count",
    "reverse_midday_trip_count",
    "forward_evening_trip_count",
    "reverse_evening_trip_count",
    "forward_overnight_trip_count",
    "reverse_overnight_trip_count",
    "forward_male_people_count",
    "reverse_male_people_count",
    "forward_female_people_count",
    "reverse_female_people_count",
    "forward_unspecified_people_count",
    "reverse_unspecified_people_count",
    "forward_18_34_people_count",
    "reverse_18_34_people_count",
    "forward_35_54_people_count",
    "reverse_35_54_people_count",
    "forward_55_64_people_count",
    "reverse_55_64_people_count",
    "forward_65_plus_people_count",
    "reverse_65_plus_people_count",
    "forward_average_speed_meters_per_second",
    "reverse_average_speed_meters_per_second",
    "osm_reference_id",
    "ride_count",
    "ebike_ride_count",
]


# ---------------------------------------------------------------------------
# ENV / HELPERS

def get_env_vars():
    script_dir = Path(__file__).parent.absolute()
    env_path = script_dir / ".env"
    load_dotenv(env_path)


def as_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def delete_if_exists(path: str):
    if arcpy.Exists(path):
        arcpy.management.Delete(path)


def find_field_case_insensitive(dataset: str, expected_name: str) -> str | None:
    expected_lower = expected_name.lower()
    for fld in arcpy.ListFields(dataset):
        if fld.name.lower() == expected_lower:
            return fld.name
    return None


def find_output_join_field(dataset: str, preferred_name: str) -> str | None:
    """
    After a join/export, ArcGIS may prefix the field name.
    Try exact match first, then suffix match.
    """
    preferred_lower = preferred_name.lower()
    fields = arcpy.ListFields(dataset)

    for fld in fields:
        if fld.name.lower() == preferred_lower:
            return fld.name

    suffix = "_" + preferred_lower
    for fld in fields:
        if fld.name.lower().endswith(suffix):
            return fld.name

    return None


def inspect_fields(feature_path: str, csv_path: str, uid_field: str, date_field: str):
    print("\nInspecting source fields...")

    feature_fields = [f.name for f in arcpy.ListFields(feature_path)]
    print(f"  Feature fields: {feature_fields}")

    # Read header line only
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        header = f.readline().strip()
    csv_fields = [c.strip() for c in header.split(",")]
    print(f"  CSV fields    : {csv_fields}")

    feature_uid = next((c for c in feature_fields if c.lower() == uid_field.lower()), None)
    csv_uid = next((c for c in csv_fields if c.lower() == uid_field.lower()), None)
    csv_date = next((c for c in csv_fields if c.lower() == date_field.lower()), None)

    if feature_uid != uid_field:
        print(f"  Resolved feature UID: '{uid_field}' -> '{feature_uid}'")
    if csv_uid != uid_field:
        print(f"  Resolved CSV UID    : '{uid_field}' -> '{csv_uid}'")
    if csv_date != date_field:
        print(f"  Resolved CSV date   : '{date_field}' -> '{csv_date}'")

    if not feature_uid:
        raise ValueError(f"Could not find UID field '{uid_field}' in feature class/shapefile.")
    if not csv_uid:
        raise ValueError(f"Could not find UID field '{uid_field}' in CSV.")
    if not csv_date:
        raise ValueError(f"Could not find date field '{date_field}' in CSV.")

    return feature_uid, csv_uid, csv_date

def release_fgdb_locks():
    arcpy.ClearWorkspaceCache_management()
    gc.collect()
    time.sleep(1)

def safe_delete_fgdb(fgdb_path):
    if not arcpy.Exists(fgdb_path):
        return

    for i in range(5):
        try:
            arcpy.ClearWorkspaceCache_management()
            shutil.rmtree(fgdb_path)
            print(f"Deleted FGDB: {fgdb_path}")
            return
        except PermissionError:
            print(f"Lock detected, retrying ({i+1}/5)...")
            time.sleep(1)

    raise RuntimeError(f"Could not delete FGDB due to persistent lock: {fgdb_path}")


def add_publish_date_field(final_fc: str, source_field: str, publish_field: str = "obs_date") -> str:
    existing = find_field_case_insensitive(final_fc, publish_field)
    if existing:
        arcpy.management.DeleteField(final_fc, existing)

    arcpy.management.AddField(final_fc, publish_field, "DATE")

    arcpy.management.CalculateField(
        final_fc,
        publish_field,
        f"!{source_field}!",
        "PYTHON3"
    )

    print(f"Created publish date field '{publish_field}' from '{source_field}'.")
    return publish_field


# ---------------------------------------------------------------------------
# FGDB BUILD

def create_staging_fgdb(output_dir: Path, fgdb_name: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    unique_fgdb_name = f"{fgdb_name}_{dt.datetime.now().strftime('%Y%m%d_%H_%M_%S')}"
    fgdb_path = output_dir / f"{unique_fgdb_name}.gdb"
    if fgdb_path.exists():
        shutil.rmtree(fgdb_path)
    safe_delete_fgdb(fgdb_path)
    arcpy.management.CreateFileGDB(str(output_dir), fgdb_path.name)
    print(f"Created FGDB: {fgdb_path}")
    return str(fgdb_path)


def copy_edges_to_fgdb(edge_shp: str, fgdb_path: str, out_name: str = "edges_base") -> str:
    out_fc = os.path.join(fgdb_path, out_name)
    delete_if_exists(out_fc)
    arcpy.conversion.FeatureClassToFeatureClass(edge_shp, fgdb_path, out_name)

    sr = arcpy.Describe(out_fc).spatialReference
    if sr.factoryCode != 4326:
        projected_fc = os.path.join(fgdb_path, f"{out_name}_wgs84")
        delete_if_exists(projected_fc)
        arcpy.management.Project(out_fc, projected_fc, arcpy.SpatialReference(4326))
        delete_if_exists(out_fc)
        out_fc = projected_fc

    print(f"Copied/projected edges to: {out_fc}")
    return out_fc


def import_csv_to_fgdb(csv_path: str, fgdb_path: str, out_name: str = "edge_counts_raw") -> str:
    out_table = os.path.join(fgdb_path, out_name)
    delete_if_exists(out_table)

    # TableToTable is older but dependable in ArcGIS Pro environments
    arcpy.conversion.TableToTable(csv_path, fgdb_path, out_name)

    print(f"Imported CSV table to: {out_table}")
    return out_table


def keep_only_needed_table_fields(table_path: str, keep_fields: list[str]):
    actual_fields = [f.name for f in arcpy.ListFields(table_path)]
    protected = {"OBJECTID", "OID", "FID"}

    to_delete = []
    keep_lower = {k.lower() for k in keep_fields}

    for fld in arcpy.ListFields(table_path):
        if fld.type in ("OID", "Geometry", "GlobalID"):
            continue
        if fld.name in protected:
            continue
        if fld.name.lower() not in keep_lower:
            to_delete.append(fld.name)

    if to_delete:
        arcpy.management.DeleteField(table_path, to_delete)
        print(f"Removed unused table fields: {len(to_delete)}")
    else:
        print("No unused table fields removed.")


def add_and_calculate_real_date(table_path: str, source_date_field: str, out_date_field: str = "date_real"):
    existing = find_field_case_insensitive(table_path, out_date_field)
    if existing:
        arcpy.management.DeleteField(table_path, existing)

    arcpy.management.AddField(table_path, out_date_field, "DATE")

    # Handles plain date strings like 2026-02-01.
    # Add more fallback formats if needed.
    code_block = r"""
import datetime

def parse_date(value):
    if value in (None, "", " "):
        return None

    text = str(value).strip()

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            pass

    raise ValueError(f"Could not parse date value: {text}")
"""

    expression = f"parse_date(!{source_date_field}!)"
    arcpy.management.CalculateField(
        table_path,
        out_date_field,
        expression,
        "PYTHON3",
        code_block
    )

    print(f"Calculated DATE field '{out_date_field}' from '{source_date_field}'.")


def build_time_enabled_feature_class(
    edges_fc: str,
    csv_table: str,
    edge_uid_field: str,
    table_uid_field: str,
    out_fc: str
) -> str:
    delete_if_exists(out_fc)

    edge_layer = "edges_layer_tmp"
    table_view = "counts_view_tmp"

    try:
        arcpy.management.MakeFeatureLayer(edges_fc, edge_layer)
        arcpy.management.MakeTableView(csv_table, table_view)

        arcpy.management.AddJoin(
            edge_layer,
            edge_uid_field,
            table_view,
            table_uid_field,
            "KEEP_COMMON"
        )

        arcpy.management.CopyFeatures(edge_layer, out_fc)
        print(f"Built final time-enabled feature class: {out_fc}")
        print(f"Feature count: {arcpy.management.GetCount(out_fc)[0]}")
        return out_fc

    finally:
        try:
            arcpy.management.Delete(edge_layer)
        except Exception:
            pass
        try:
            arcpy.management.Delete(table_view)
        except Exception:
            pass

        arcpy.ClearWorkspaceCache_management()


def zip_fgdb(fgdb_path: str, zip_path: str) -> str:
    if os.path.exists(zip_path):
        os.remove(zip_path)

    fgdb_folder = Path(fgdb_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in fgdb_folder.rglob("*"):
            if path.is_dir():
                continue

            # Skip transient ArcGIS lock files
            if path.name.endswith(".lock") or ".lock" in path.name.lower():
                continue

            arcname = str(Path(fgdb_folder.name) / path.relative_to(fgdb_folder))
            zf.write(path, arcname)

    print(f"Created zip: {zip_path}")
    return zip_path


# ---------------------------------------------------------------------------
# AGOL PUBLISHING

def get_published_date_field_name(flc: FeatureLayerCollection, preferred_name: str = "date") -> str:
    lyr = flc.layers[0]
    fields = lyr.properties.fields

    # First try preferred name
    for f in fields:
        if f["name"].lower() == preferred_name.lower() and f["type"] == "esriFieldTypeDate":
            return f["name"]

    # Otherwise take the first actual date field
    for f in fields:
        if f["type"] == "esriFieldTypeDate":
            print(f"Using published date field '{f['name']}'")
            return f["name"]

    available = [(f["name"], f["type"]) for f in fields]
    raise ValueError(f"No DATE field found on published layer. Available fields: {available}")


def assert_date_field(flc: FeatureLayerCollection, field_name: str):
    lyr = flc.layers[0]
    fld = next((f for f in lyr.properties.fields if f["name"].lower() == field_name.lower()), None)

    if not fld:
        available = [f.name for f in lyr.properties.fields]
        raise ValueError(
            f"Published layer does not contain field '{field_name}'. "
            f"Available fields: {available}"
        )

    if fld["type"] != "esriFieldTypeDate":
        raise ValueError(
            f"Field '{field_name}' published as {fld['type']}, not a date field."
        )

    print(f"Published field '{field_name}' type: {fld['type']}")


def enable_time_on_layer(flc: FeatureLayerCollection, date_field_name: str):
    lyr = flc.layers[0]
    time_info = {
        "startTimeField": date_field_name,
        "endTimeField": "",
        "trackIdField": "",
        "timeInterval": 1,
        "timeIntervalUnits": "esriTimeUnitsDays",
        "exportOptions": {
            "useTime": True,
            "timeDataCumulative": False,
            "timeOffset": 0,
            "timeOffsetUnits": "esriTimeUnitsDays"
        }
    }
    lyr.manager.update_definition({"timeInfo": time_info})
    print(f"Time enabled using field: {date_field_name}")


def publish_or_overwrite_fgdb(
    gis: GIS,
    zip_path: str,
    title: str,
    time_field_name: str,
    share_org: bool
) -> str:
    existing = gis.content.search(
        f'title:"{title}" owner:{gis.users.me.username}',
        item_type="Feature Layer"
    )
    existing = [i for i in existing if i.title == title]

    if existing:
        item = existing[0]
        print(f"Overwriting existing item: {item.id}")
        flc = FeatureLayerCollection.fromitem(item)
        flc.manager.overwrite(zip_path)
    else:
        print(f"Publishing new item: '{title}'")
        fgdb_item = gis.content.add(
            item_properties={
                "title": title,
                "type": "File Geodatabase",
                "tags": "Strava Metro, active transportation, time-enabled"
            },
            data=zip_path
        )
        item = fgdb_item.publish()
        fgdb_item.delete()

    item.share(org=share_org, everyone=False)

    flc = FeatureLayerCollection.fromitem(item)
    #actual_time_field = get_published_date_field_name(flc, time_field_name)
    #assert_date_field(flc, actual_time_field)
    #enable_time_on_layer(flc, actual_time_field)

    #assert_date_field(flc, actual_time_field)
    # TODO - replace hardcoded name of datetime layer with variable later
    enable_time_on_layer(flc, "obs_date")

    url = f"https://www.arcgis.com/home/item.html?id={item.id}"
    print(f"Published: {url}")
    return item.id


# ---------------------------------------------------------------------------
# MAIN

def main():
    start_time = dt.datetime.now()
    print(f"Started processing Strava Metro data at {start_time}")
    print("=== Strava Metro -> ArcGIS Online (FGDB version) ===\n")

    get_env_vars()

    gis = GIS("home")
    print(f"Logged in as: {gis.users.me.username}\n")

    edge_shp = os.getenv("EDGE_SHP")
    edge_csv = os.getenv("EDGE_CSV")
    edge_title = os.getenv("EDGE_TITLE")
    output_dir = Path(os.getenv("OUTPUT_DIR"))
    share_org = as_bool(os.getenv("SHARE_ORG", "true"))

    uid_field = "edge_uid"
    csv_date_field = os.getenv("DATE_FIELD")  # e.g. "date"
    fgdb_date_field = "date_real"

    if not edge_shp or not edge_csv or not edge_title or not csv_date_field:
        raise ValueError("Missing one or more required .env values: EDGE_SHP, EDGE_CSV, EDGE_TITLE, DATE_FIELD")

    feature_uid_field, csv_uid_field, resolved_csv_date_field = inspect_fields(
        edge_shp, edge_csv, uid_field, csv_date_field
    )

    fgdb_path = create_staging_fgdb(output_dir, "strava_staging")
    edges_fc = copy_edges_to_fgdb(edge_shp, fgdb_path, "edges_base")
    csv_table = import_csv_to_fgdb(edge_csv, fgdb_path, "edge_counts_raw")

    keep_fields = [csv_uid_field, resolved_csv_date_field] + EDGE_COUNT_FIELDS
    keep_only_needed_table_fields(csv_table, keep_fields)

    add_and_calculate_real_date(csv_table, resolved_csv_date_field, fgdb_date_field)

    final_fc = os.path.join(fgdb_path, "strava_edges_time")
    build_time_enabled_feature_class(
        edges_fc=edges_fc,
        csv_table=csv_table,
        edge_uid_field=feature_uid_field,
        table_uid_field=csv_uid_field,
        out_fc=final_fc
    )

    # TODO - remove if not necessary
    arcpy.ClearWorkspaceCache_management()

    # After join/export, field names may be prefixed. Find the actual date field name in output.
    joined_date_field = find_output_join_field(final_fc, fgdb_date_field)
    if not joined_date_field:
        available = [f.name for f in arcpy.ListFields(final_fc)]
        raise ValueError(
            f"Could not find output time field derived from '{fgdb_date_field}'. "
            f"Available fields: {available}"
        )

    print(f"Resolved joined date field: {joined_date_field}")

    published_time_field = add_publish_date_field(final_fc, joined_date_field, "obs_date")
    print(f"Using publish date field: {published_time_field}")

    zip_path = str(output_dir / "strava_edges_fgdb.zip")
        # Find the published time field name first
    published_time_field = find_output_join_field(final_fc, fgdb_date_field)
    if not published_time_field:
        available = [f.name for f in arcpy.ListFields(final_fc)]
        raise ValueError(
            f"Could not find output time field derived from '{fgdb_date_field}'. "
            f"Available fields: {available}"
        )

    print(f"Resolved final time field: {published_time_field}")

    # Release references to datasets inside the FGDB before zipping
    del edges_fc
    del csv_table
    del final_fc

    release_fgdb_locks()
    zip_fgdb(fgdb_path, zip_path)

    edge_id = publish_or_overwrite_fgdb(
        gis=gis,
        zip_path=zip_path,
        title=edge_title,
        time_field_name=published_time_field,
        share_org=share_org
    )

    print(f"\nDone.\nEdge layer: https://www.arcgis.com/home/item.html?id={edge_id}")

    end_time = dt.datetime.now()
    print(f"Completed process at {end_time} in {end_time - start_time}\n")


if __name__ == "__main__":
    main()