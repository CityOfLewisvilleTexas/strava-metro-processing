"""
strava_metro_to_ago.py

Joins Strava Metro tabular data to edge/hexagon shapefiles,
produces time-enabled GeoDataFrames (one row per edge/hex per date),
and publishes or overwrites hosted feature layers in ArcGIS Online.

Dependencies:
    pip install geopandas pandas arcgis python-dotenv
"""

import os
import json
import zipfile
from pathlib import Path

import pandas as pd
import geopandas as gpd
from arcgis.gis import GIS
from arcgis.features import FeatureLayerCollection

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# CONFIG — edit these before running

EDGE_COUNT_FIELDS = ['total_trip_count', 'forward_trip_count', 'reverse_trip_count', 'forward_people_count', 'reverse_people_count', 'forward_commute_trip_count', 'reverse_commute_trip_count', 'forward_leisure_trip_count', 'reverse_leisure_trip_count', 'forward_morning_trip_count', 'reverse_morning_trip_count', 'forward_midday_trip_count', 'reverse_midday_trip_count', 'forward_evening_trip_count', 'reverse_evening_trip_count', 'forward_overnight_trip_count', 'reverse_overnight_trip_count', 'forward_male_people_count', 'reverse_male_people_count', 'forward_female_people_count', 'reverse_female_people_count', 'forward_unspecified_people_count', 'reverse_unspecified_people_count', 'forward_18_34_people_count', 'reverse_18_34_people_count', 'forward_35_54_people_count', 'reverse_35_54_people_count', 'forward_55_64_people_count', 'reverse_55_64_people_count', 'forward_65_plus_people_count', 'reverse_65_plus_people_count', 'forward_average_speed_meters_per_second', 'reverse_average_speed_meters_per_second', 'osm_reference_id', 'ride_count', 'ebike_ride_count',]
# ---------------------------------------------------------------------------


def get_env_vars():
    """
    Get environment variables from a .env file located in the same directory as this script.
    """
    script_dir = Path(__file__).parent.absolute()
    env_path = script_dir / '.env'
    load_dotenv(env_path)


def inspect_fields(shp_path: str, csv_path: str, uid_field: str):
    """Print actual field names to catch truncation / case mismatches."""
    gdf = gpd.read_file(shp_path)
    df  = pd.read_csv(csv_path, nrows=0)
    print(f"\n  Shapefile columns : {list(gdf.columns)}")
    print(f"  CSV columns       : {list(df.columns)}")

    # Try to find the uid_field case-insensitively
    shp_match = next((c for c in gdf.columns if c.lower() == uid_field.lower()), None)
    csv_match = next((c for c in df.columns  if c.lower() == uid_field.lower()), None)

    if shp_match != uid_field:
        print(f"  ⚠ Shapefile uid field resolved: '{uid_field}' → '{shp_match}'")
    if csv_match != uid_field:
        print(f"  ⚠ CSV uid field resolved: '{uid_field}' → '{csv_match}'")

    return shp_match, csv_match


def normalize_columns(gdf: gpd.GeoDataFrame, df: pd.DataFrame) -> tuple:
    """Strip whitespace and lowercase all column names."""
    gdf.columns = [c.strip().lower() for c in gdf.columns]
    df.columns  = [c.strip().lower() for c in df.columns]
    return gdf, df


def read_and_join(shp_path: str, csv_path: str, uid_field: str,
                  count_fields: list, date_field: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    df = pd.read_csv(csv_path)

    gdf, df = normalize_columns(gdf, df)

    keep_cols = [uid_field, date_field] + [f for f in count_fields if f in df.columns]
    df = df[keep_cols].copy()

    # Convert to true date-only values for shapefile compatibility
    df[date_field] = pd.to_datetime(df[date_field], errors="raise").dt.date

    merged = gdf.merge(df, on=uid_field, how="inner")

    print(f"  Shapefile rows : {len(gdf):,}")
    print(f"  CSV rows       : {len(df):,}")
    print(f"  Merged rows    : {len(merged):,}")
    print(f"  Date range     : {merged[date_field].min()} → {merged[date_field].max()}")

    return merged


def gdf_to_zipped_shp(gdf: gpd.GeoDataFrame, stem: str, out_dir: str
                      ) -> tuple[str, dict]:
    """
    Write GeoDataFrame to a zipped shapefile (.zip) for AGOL upload.
    Shapefile field names are truncated to 10 chars; a sidecar JSON
    records the mapping back to full names.
    Returns (zip_path, alias_map) where alias_map is
    {truncated_name: original_name} for every field that was renamed.
    Pass alias_map to apply_field_aliases() after publishing.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    shp_dir = os.path.join(out_dir, stem)
    os.makedirs(shp_dir, exist_ok=True)

    cols = [c for c in gdf.columns if c != "geometry"]
    rename_map = {c: c[:10] for c in cols if len(c) > 10}

    if rename_map:
        truncated = list(rename_map.values())
        if len(truncated) != len(set(truncated)):
            raise ValueError(
                f"Field name truncation collision detected: {rename_map}\n"
                "Use GeoJSON output (gdf_to_geojson) for wide format."
            )
        out_gdf = gdf.rename(columns=rename_map)
        # alias_map: truncated → original (for AGOL field alias update)
        alias_map = {v: k for k, v in rename_map.items()}
        with open(os.path.join(shp_dir, "field_name_map.json"), "w") as f:
            json.dump(alias_map, f, indent=2)
        print(f"  Field name truncations (short → original): {alias_map}")
    else:
        out_gdf   = gdf.copy()
        alias_map = {}

    shp_path = os.path.join(shp_dir, f"{stem}.shp")
    out_gdf.to_file(shp_path, driver="ESRI Shapefile")

    zip_path = os.path.join(out_dir, f"{stem}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in Path(shp_dir).iterdir():
            zf.write(f, arcname=f.name)
    print(f"  Written: {zip_path}")
    return zip_path, alias_map


def apply_field_aliases(flc: FeatureLayerCollection, alias_map: dict):
    """
    Push field aliases to a hosted feature layer so that the original
    (pre-truncation) field names are displayed in Map Viewer / popups.
    alias_map: {truncated_field_name: original_field_name}
    """
    if not alias_map:
        return
    lyr = flc.layers[0]
    current_fields = lyr.properties.fields
    updated_fields = []
    for field in current_fields:
        # Round-trip through JSON to convert PropertyMap (and any nested
        # PropertyMap values) into plain dicts that are JSON-serializable
        plain = json.loads(json.dumps(dict(field)))
        if plain["name"] in alias_map:
            plain["alias"] = alias_map[plain["name"]]
        updated_fields.append(plain)
    lyr.manager.update_definition({"fields": updated_fields})
    print(f"  Field aliases applied: {alias_map}")


def assert_date_field(flc: FeatureLayerCollection, field_name: str):
    lyr = flc.layers[0]
    fld = next((f for f in lyr.properties.fields if f["name"].lower() == field_name.lower()), None)
    if not fld:
        raise ValueError(f"Field '{field_name}' not found on published layer.")
    print(f"Published field '{field_name}' type: {fld['type']}")
    if fld["type"] not in ("esriFieldTypeDate",):
        raise ValueError(
            f"Field '{field_name}' published as {fld['type']}, not a date field."
        )
    

def enable_time_on_layer(flc: FeatureLayerCollection, date_field_short: str):
    """
    Enable time settings on the first layer of a FeatureLayerCollection.
    date_field_short: the (possibly truncated to 10-char) date field name.
    """
    lyr = flc.layers[0]
    time_info = {
        "startTimeField": date_field_short,
        "endTimeField": "",
        "trackIdField": "",
        "timeInterval": 1,
        "timeIntervalUnits": "esriTimeUnitsDays",
        "timeReference": None,
        "exportOptions": {
            "useTime": True,
            "timeDataCumulative": False,
            "timeOffset": 0,
            "timeOffsetUnits": "esriTimeUnitsDays"
        }
    }
    lyr.manager.update_definition({"timeInfo": time_info})
    print(f"  Time enabled on layer: startTimeField='{date_field_short}'")


def publish_or_overwrite(gis: GIS, zip_path: str, title: str,
                         date_field_short: str, share_org: bool,
                         alias_map: dict = None) -> str:
    """
    If an item with `title` already exists for this user, overwrite it.
    Otherwise, publish fresh. Returns the item ID.
    """
    existing = gis.content.search(f'title:"{title}" owner:{gis.users.me.username}',
                                  item_type="Feature Layer")
    existing = [i for i in existing if i.title == title]

    if existing:
        item = existing[0]
        print(f"  Overwriting existing item: {item.id}")
        flc = FeatureLayerCollection.fromitem(item)
        flc.manager.overwrite(zip_path)
    else:
        print(f"  Publishing new item: '{title}'")
        shp_item = gis.content.add(
            item_properties={"title": title, "type": "Shapefile",
                             "tags": "Strava Metro, active transportation, time-enabled"},
            data=zip_path
        )
        item = shp_item.publish()
        shp_item.delete()

    # Share (everyone=False — public sharing violates Strava Metro terms)
    #item.share(org=share_org, everyone=False)
    item.share(org=True, everyone=False)

    flc = FeatureLayerCollection.fromitem(item)

    # Apply original field names as aliases
    if alias_map:
        apply_field_aliases(flc, alias_map)

    assert_date_field(flc, date_field_short)
    # Enable time slider
    enable_time_on_layer(flc, date_field_short)

    url = f"https://www.arcgis.com/home/item.html?id={item.id}"
    print(f"  Published: {url}")
    return item.id


def get_short_field(field_name: str) -> str:
    """Return the shapefile-truncated (10-char) version of a field name."""
    return field_name[:10]


def main():
    print("=== Strava Metro → ArcGIS Online ===\n")

    get_env_vars()
    gis = GIS('home')
    print(f"Logged in as: {gis.users.me.username}\n")

    date_field = os.getenv("DATE_FIELD")
    edge_shp = os.getenv("EDGE_SHP")
    edge_csv = os.getenv("EDGE_CSV")
    #edge_count_fields = os.getenv("EDGE_COUNT_FIELDS")
    output_dir = Path(os.getenv("OUTPUT_DIR"))
    uid_field = "edge_uid"
    output_suffix = "_" + output_dir.name

    inspect_fields(edge_shp, edge_csv, uid_field)

    #date_short = get_short_field(date_field)

    # -- EDGES ----------------------------------------------------------------
    print("Processing edges...")
    edge_gdf = read_and_join(
        shp_path=edge_shp, csv_path=edge_csv,
        uid_field=uid_field, count_fields=EDGE_COUNT_FIELDS,
        date_field=date_field
    )
    edge_zip, alias_map = gdf_to_zipped_shp(edge_gdf, "strava_edges" + output_suffix, output_dir)
    edge_id = publish_or_overwrite(gis, edge_zip, os.getenv("EDGE_TITLE"),
                                   get_short_field(date_field), os.getenv("SHARE_ORG"),
                                   alias_map=alias_map)

    # -- HEXAGONS -------------------------------------------------------------
    #print("\nProcessing hexagons...")
    #hex_gdf = read_and_join(
    #    shp_path=HEX_SHP, csv_path=HEX_CSV,
    #    uid_field="hex_uid", count_fields=HEX_COUNT_FIELDS,
    #    date_field=date_field
    #)
    #hex_zip, hex_alias_map = gdf_to_zipped_shp(hex_gdf, "strava_hexagons" + output_suffix, output_dir)
    #hex_id = publish_or_overwrite(gis, hex_zip, HEX_TITLE,
    #                              get_short_field(date_field), SHARE_ORG,
    #                              alias_map=hex_alias_map)
    #print(f"\nDone.\n  Edge layer  : https://www.arcgis.com/home/item.html?id={edge_id}")
    #print(f"  Hex layer   : https://www.arcgis.com/home/item.html?id={hex_id}")


if __name__ == "__main__":
    main()