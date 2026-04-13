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
    """
    Read shapefile + CSV, merge on uid_field.
    Returns a long-format GeoDataFrame with geometry replicated per date row.
    Date column is cast to datetime for AGOL time-slider compatibility.
    """
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    df = pd.read_csv(csv_path, parse_dates=[date_field])

    gdf, df = normalize_columns(gdf, df)

    # Keep only relevant columns from CSV
    keep_cols = [uid_field, date_field] + [f for f in count_fields if f in df.columns]
    print(f'columns to be preserved: {keep_cols}')
    df = df[keep_cols].copy()
    print(f'data frame head: {df.head}')
    print(f'data frame tail: {df.tail}')

    # Merge — geometry is repeated for every date row (required for time slider)
    merged = gdf.merge(df, on=uid_field, how="inner")
    merged[date_field] = pd.to_datetime(merged[date_field])

    print(f"  Shapefile rows : {len(gdf):,}")
    print(f"  CSV rows       : {len(df):,}")
    print(f"  Merged rows    : {len(merged):,}")
    print(f"  Date range     : {merged[date_field].min().date()} → "
          f"{merged[date_field].max().date()}")
    return merged


def gdf_to_zipped_shp(gdf: gpd.GeoDataFrame, stem: str, out_dir: str) -> str:
    """
    Write GeoDataFrame to a zipped shapefile (.zip) for AGOL upload.
    Shapefile field names are truncated to 10 chars; a sidecar JSON
    records the mapping back to full names.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    shp_dir = os.path.join(out_dir, stem)
    os.makedirs(shp_dir, exist_ok=True)

    # Shapefile field name length limit: 10 chars
    rename_map = {}
    cols = [c for c in gdf.columns if c != "geometry"]
    for col in cols:
        short = col[:10]
        if short != col:
            rename_map[col] = short

    out_gdf = gdf.copy()
    if rename_map:
        out_gdf = out_gdf.rename(columns=rename_map)
        # Save mapping for reference
        with open(os.path.join(shp_dir, "field_name_map.json"), "w") as f:
            json.dump(rename_map, f, indent=2)
        print(f"  Field names truncated: {rename_map}")

    shp_path = os.path.join(shp_dir, f"{stem}.shp")
    out_gdf.to_file(shp_path, driver="ESRI Shapefile")

    zip_path = os.path.join(out_dir, f"{stem}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in Path(shp_dir).iterdir():
            zf.write(f, arcname=f.name)
    print(f"  Written: {zip_path}")
    return zip_path


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
                         date_field_short: str,
                         share_org: bool) -> str:
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

    # Share (value for everyone is hardcoded as public sharing violates Strava Metro terms)
    item.share(org=share_org, everyone=False)

    # Enable time slider
    flc = FeatureLayerCollection.fromitem(item)
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
    #gis = GIS(PORTAL_URL, USERNAME, PASSWORD)
    #print(f"Logged in as: {gis.users.me.username}\n")

    date_field = os.getenv("DATE_FIELD")
    edge_shp = os.getenv("EDGE_SHP")
    edge_csv = os.getenv("EDGE_CSV")
    #edge_count_fields = os.getenv("EDGE_COUNT_FIELDS")
    output_dir = Path(os.getenv("OUTPUT_DIR"))
    uid_field = "edge_uid"
    output_suffix = "_" + output_dir.name

    inspect_fields(edge_shp, edge_csv, uid_field)

    #date_short = get_short_field()

    # -- EDGES ----------------------------------------------------------------
    print("Processing edges...")
    edge_gdf = read_and_join(
        shp_path=edge_shp, csv_path=edge_csv,
        uid_field=uid_field, count_fields=EDGE_COUNT_FIELDS,
        date_field=date_field
    )
    edge_zip = gdf_to_zipped_shp(edge_gdf, "strava_edges" + output_suffix, output_dir)
    #edge_id  = publish_or_overwrite(gis, edge_zip, EDGE_TITLE,
    #                                date_short, SHARE_ORG)

    # -- HEXAGONS -------------------------------------------------------------
    #print("\nProcessing hexagons...")
    #hex_gdf = read_and_join(
    #    shp_path=HEX_SHP, csv_path=HEX_CSV,
    #    uid_field="hex_uid", count_fields=HEX_COUNT_FIELDS,
    #    date_field=DATE_FIELD
    #)
    #hex_zip = gdf_to_zipped_shp(hex_gdf, "strava_hexagons" + output_suffix, OUTPUT_DIR)
    #hex_id  = publish_or_overwrite(gis, hex_zip, HEX_TITLE,
    #                               date_short, SHARE_ORG)
    #print(f"\nDone.\n  Edge layer  : https://www.arcgis.com/home/item.html?id={edge_id}")
    #print(f"  Hex layer   : https://www.arcgis.com/home/item.html?id={hex_id}")


if __name__ == "__main__":
    main()