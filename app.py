import io
import streamlit as st
import boto3
import botocore
import os
import gzip
import shutil
import requests
from datetime import datetime, timedelta, time
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.path import Path
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
import geopandas as gpd
from shapely.geometry import Polygon
from skimage import measure
import gc
from scipy.ndimage import uniform_filter, binary_dilation

# --- 1. PAGE CONFIG & CACHED LOADERS ---
st.set_page_config(page_title="TCF Verification Dashboard", layout="wide", page_icon="✈️")
st.title("Objective TCF Verification Dashboard")

# UPGRADE: cache_resource is much safer for large map files than cache_data
@st.cache_resource
def load_geography():
    """Loads States and ARTCC boundaries once and keeps them in memory"""
    states = gpd.GeoDataFrame(geometry=[])
    artccs = gpd.GeoDataFrame(geometry=[])
    
    try:
        url = "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json"
        response = requests.get(url, timeout=10)
        states = gpd.read_file(io.BytesIO(response.content))
    except Exception as e:
        st.sidebar.error(f"State boundaries error: {e}")
        
    try:
        # Check 1: Does the file even exist on the Linux server?
        if not os.path.exists("artcc1.geojson"):
            st.sidebar.error("❌ ERROR: 'artcc1.geojson' is missing from the root directory! Check GitHub capitalization.")
        else:
            # Check 2: Load the file
            artccs = gpd.read_file("artcc1.geojson")
            
            # Check 3: Safely handle the CRS (Coordinate Reference System)
            if artccs.crs is None:
                artccs.set_crs("EPSG:4326", inplace=True)
            else:
                try:
                    artccs = artccs.to_crs("EPSG:4326")
                except Exception:
                    pass # If it fails, the file is likely already in standard Lat/Lon (CRS84)
                    
    except Exception as e:
        # This will print the EXACT reason it's failing to your screen!
        st.sidebar.error(f"❌ ARTCC Parsing Error: {e}")
        
    return states, artccs

gdf_states, gdf_artcc = load_geography()

# --- 2. HELPER FUNCTIONS ---
def parse_iem_cow_text(text_data):
    """Parses legacy NWS/AWIPS AREA text into a GeoDataFrame"""
    polygons = []
    for line in text_data.split('\n'):
        line = line.strip()
        if line.startswith("AREA"):
            parts = line.split()
            try:
                num_points = int(parts[7])
                coords = []
                idx = 8
                for _ in range(num_points):
                    if idx + 1 < len(parts):
                        lat = float(parts[idx]) / 10.0
                        lon = float(parts[idx+1]) / 10.0
                        if lon > 0: lon = -lon
                        coords.append((lon, lat))
                        idx += 2
                if len(coords) >= 3:
                    polygons.append(Polygon(coords))
            except Exception:
                continue 
                
    if polygons:
        return gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    else:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

def get_artccs(poly, artcc_gdf):
    """Finds which ARTCCs a polygon intersects"""
    if artcc_gdf.empty: return "UNKNOWN"
    intersecting = artcc_gdf[artcc_gdf.intersects(poly)]
    if intersecting.empty: return "UNKNOWN"
    centers = intersecting['IDENT'].dropna().unique().tolist()
    return "/".join(centers)

def download_mrms_scan(product, dt_obj, dest_dir="mrms_data"):
    os.makedirs(dest_dir, exist_ok=True)
    date_str = dt_obj.strftime('%Y%m%d')
    bucket_name = 'noaa-mrms-pds'
    prefix = f"CONUS/{product}_00.50/{date_str}/"
    s3 = boto3.client('s3', config=botocore.client.Config(signature_version=botocore.UNSIGNED))
    
    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        if 'Contents' not in response: return None
            
        target_time_str = f"{date_str}-{dt_obj.strftime('%H%M')}"
        best_key = None
        for obj in response['Contents']:
            if target_time_str in obj['Key'] and obj['Key'].endswith('.grib2.gz'):
                best_key = obj['Key']
                break
                
        if not best_key: return None

        local_gz = os.path.join(dest_dir, best_key.split('/')[-1])
        local_grib = local_gz.replace('.gz', '')
        
        if not os.path.exists(local_grib):
            s3.download_file(bucket_name, best_key, local_gz)
            with gzip.open(local_gz, 'rb') as f_in, open(local_grib, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(local_gz)
        return local_grib
    except Exception:
        return None

def fetch_tcf_geojson(date_obj, issue_hr, f_hr):
    date_str = date_obj.strftime("%Y%m%d")
    issue_str = f"{issue_hr:02d}"
    url = f"https://aviationweather.gov/api/data/tcf?date={date_str}&issue={issue_str}&fhr={f_hr}&format=geojson"
    
    try:
        headers = {
            "User-Agent": "TCFVerificationDashboard/1.0",
            "Accept": "application/geo+json"
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return gpd.read_file(io.BytesIO(response.content))
        else:
            st.sidebar.error(f"AWC API Rejected: HTTP {response.status_code}") 
    except Exception as e:
        st.sidebar.error(f"AWC Connection Error: {e}")
        
    return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("Event Selection")
target_date = st.sidebar.date_input("Select Event Date", datetime(2026, 5, 23))
issuance_hour = st.sidebar.selectbox("Issuance Time (Z)", [5, 7, 9, 11, 13, 15, 17, 19, 21, 23], index=7)
lead_time = st.sidebar.radio("Forecast Hour", [4, 6, 8])

valid_time = issuance_hour + lead_time
if valid_time >= 24:
    valid_time -= 24
    valid_dt = datetime.combine(target_date + timedelta(days=1), time(valid_time, 0))
else:
    valid_dt = datetime.combine(target_date, time(valid_time, 0))

st.sidebar.markdown(f"**Valid Time (VT):** {valid_dt.strftime('%b %d, %H:00Z')}")

st.sidebar.markdown("---")
st.sidebar.subheader("Manual Data Override")
# Placed permanently in the sidebar so Streamlit doesn't reset when a file is dropped!
uploaded_file = st.sidebar.file_uploader("Upload TCF (.geojson or .txt)", type=['geojson', 'txt'])

# --- 4. MAIN EXECUTION ---
if st.sidebar.button("Run Verification"):

    with st.status("Fetching Data...", expanded=True) as status:
        
        # --- Step A: Get Forecast (Check Uploader First) ---
        if uploaded_file is not None:
            st.write("Processing manually uploaded file...")
            if uploaded_file.name.endswith('.txt'):
                raw_text = uploaded_file.getvalue().decode("utf-8")
                gdf_forecast = parse_iem_cow_text(raw_text)
                st.success("IEM Cow Text File translated and loaded!")
            else:
                gdf_forecast = gpd.read_file(uploaded_file)
                st.success("GeoJSON loaded successfully!")
        else:
            st.write("Downloading AWC TCF Forecast...")
            gdf_forecast = fetch_tcf_geojson(target_date, issuance_hour, lead_time)
            if gdf_forecast.empty:
                st.error("AWC API Failed. Please upload a .txt or .geojson file in the sidebar and try again.")
                st.stop()

        # --- Step B: Rolling Composite ---
        time_offsets = list(range(-15, 16, 5))
        max_tops, max_refl = None, None
        lons, lats = None, None
        step = 5 
        
        for offset in time_offsets:
            scan_dt = valid_dt + timedelta(minutes=offset)
            st.write(f"Pulling MRMS for {scan_dt.strftime('%H:%MZ')}...")
            tops_file = download_mrms_scan("EchoTop_18", scan_dt)
            refl_file = download_mrms_scan("MergedReflectivityQCComposite", scan_dt)
            
            if tops_file and refl_file:
                ds_t = xr.open_dataset(tops_file, engine='cfgrib', backend_kwargs={'indexpath': ''})
                ds_r = xr.open_dataset(refl_file, engine='cfgrib', backend_kwargs={'indexpath': ''})
                
                curr_tops = ds_t.unknown[::step, ::step].values * 3.28084
                curr_refl = ds_r.unknown[::step, ::step].values
                
                if lons is None:
                    lons = ds_t.longitude[::step].values
                    lons = np.where(lons > 180, lons - 360, lons)
                    lats = ds_t.latitude[::step].values
                    
                if max_tops is None:
                    max_tops, max_refl = curr_tops, curr_refl
                else:
                    max_tops = np.maximum(max_tops, curr_tops)
                    max_refl = np.maximum(max_refl, curr_refl)
                    
                ds_t.close()
                ds_r.close()
                del ds_t, ds_r, curr_tops, curr_refl
                gc.collect()

        st.write("Building Objective Truth Polygons...")
        if os.path.exists("mrms_data"):
            shutil.rmtree("mrms_data")
            
        status.update(label="Data processing complete!", state="complete", expanded=False)

    # --- Step C: Verification Math ---
    with st.spinner("Calculating Spatial Overlap & Echo Tops..."):
        valid_convection = (max_refl >= 40)
        top_verif_matrix = np.zeros_like(max_tops, dtype=int)
        top_verif_matrix[valid_convection & (max_tops >= 25) & (max_tops < 30)] = 1  
        top_verif_matrix[valid_convection & (max_tops >= 30) & (max_tops < 35)] = 2  
        top_verif_matrix[valid_convection & (max_tops >= 35) & (max_tops < 40)] = 3  
        top_verif_matrix[valid_convection & (max_tops >= 40)] = 4  

        raw_cores = ((max_refl >= 40) & (max_tops >= 25))
        buffered_cores = binary_dilation(raw_cores, iterations=1)
        coverage_fraction = uniform_filter(buffered_cores.astype(float), size=20)

        def extract_tcf_polygons(coverage_mask, min_area_m2=0):
            contours = measure.find_contours(coverage_mask, 0.5)
            polygons = []
            for contour in contours:
                if len(contour) > 10: 
                    poly = Polygon(zip([lons[int(p[1])] for p in contour], [lats[int(p[0])] for p in contour]))
                    if poly.is_valid: polygons.append(poly.simplify(0.05))
            gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
            if gdf.is_empty.all(): return gdf
            
            gdf_m = gdf.to_crs("EPSG:5070")
            if min_area_m2 > 0:
                valid_area = gdf_m.geometry.area >= min_area_m2
                gdf = gdf[valid_area]
            if not gdf.is_empty.all():
                gdf = gpd.GeoDataFrame(geometry=[gdf.unary_union], crs="EPSG:4326")
            return gdf

        gdf_sparse = extract_tcf_polygons((coverage_fraction >= 0.25).astype(int), min_area_m2=10_000_000_000)
        del coverage_fraction, raw_cores, buffered_cores
        gc.collect()

        truth_union = gdf_sparse.unary_union if not gdf_sparse.is_empty.all() else Polygon()
        fcst_union = gdf_forecast.unary_union if not gdf_forecast.is_empty.all() else Polygon()

        graded_forecasts, graded_misses = [], []
        
        for idx, row in (gdf_forecast.explode(index_parts=False).reset_index(drop=True) if not gdf_forecast.is_empty.all() else gpd.GeoDataFrame(geometry=[])).iterrows():
            poly = row.geometry
            if poly.is_empty: continue
            
            fcst_area = poly.area
            hit_area = poly.intersection(truth_union).area
            coverage = hit_area / fcst_area if fcst_area > 0 else 0
            
            min_lon, min_lat, max_lon, max_lat = poly.bounds
            lat_mask, lon_mask = (lats >= min_lat) & (lats <= max_lat), (lons >= min_lon) & (lons <= max_lon)
            subset_tops, subset_refl = max_tops[lat_mask][:, lon_mask], max_refl[lat_mask][:, lon_mask]
            lon_grid, lat_grid = np.meshgrid(lons[lon_mask], lats[lat_mask])
            
            in_poly_mask = Path(np.array(poly.exterior.coords)).contains_points(np.vstack((lon_grid.flatten(), lat_grid.flatten())).T).reshape(lon_grid.shape)
            valid_tops = subset_tops[in_poly_mask & (subset_refl >= 40) & (subset_tops >= 25)]
            
            actual_top_kft = np.percentile(valid_tops, 90) if len(valid_tops) > 5 else 0
            
            cat, color = ("Verified Well", 'lime') if coverage >= 0.50 else ("Verified Close", 'yellow') if coverage >= 0.20 else ("Overforecasted", 'orange')
            graded_forecasts.append({'geometry': poly, 'category': cat, 'color': color, 'idx': idx+1, 'top': actual_top_kft})

        for idx, row in (gdf_sparse.explode(index_parts=False).reset_index(drop=True) if not gdf_sparse.is_empty.all() else gpd.GeoDataFrame(geometry=[])).iterrows():
            poly = row.geometry
            if poly.is_empty: continue
            if (poly.intersection(fcst_union).area / poly.area if poly.area > 0 else 0) < 0.20:
                graded_misses.append({'geometry': poly, 'category': 'Missed', 'color': 'red', 'idx': idx+1})

        gdf_graded_fcst = gpd.GeoDataFrame(graded_forecasts, crs="EPSG:4326") if graded_forecasts else gpd.GeoDataFrame(geometry=[])
        gdf_graded_miss = gpd.GeoDataFrame(graded_misses, crs="EPSG:4326") if graded_misses else gpd.GeoDataFrame(geometry=[])

    # --- Step D: Visual Render ---
    st.markdown("---")
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Objective Verification Scorecard Map")
        fig, ax = plt.subplots(figsize=(16, 10))
        cmap_heights = ListedColormap(['#000000', '#00FFFF', '#FFFF00', '#FF8000', '#FF0000'])
        ax.pcolormesh(lons, lats, top_verif_matrix, cmap=cmap_heights, vmin=0, vmax=4, shading='auto')

        if not gdf_states.empty: gdf_states.plot(ax=ax, facecolor='none', edgecolor='#505050', linewidth=1, zorder=2)
        if not gdf_artcc.empty: gdf_artcc.plot(ax=ax, facecolor='none', edgecolor='yellow', linewidth=1.5, linestyle=':', zorder=3)

        if not gdf_graded_fcst.empty:
            for _, row in gdf_graded_fcst.iterrows():
                gpd.GeoSeries([row.geometry]).plot(ax=ax, facecolor='none', edgecolor=row.color, linewidth=3, zorder=5)
                gpd.GeoSeries([row.geometry]).plot(ax=ax, facecolor='none', edgecolor='white', linewidth=1, linestyle='--', zorder=6)
                centroid = row.geometry.centroid
                ax.text(centroid.x, centroid.y, str(row.idx), color='white', fontsize=14, fontweight='bold', ha='center', va='center', zorder=10, bbox=dict(facecolor='black', alpha=0.6, edgecolor='none', boxstyle='round,pad=0.3'))

        if not gdf_graded_miss.empty:
            for _, row in gdf_graded_miss.iterrows():
                gpd.GeoSeries([row.geometry]).plot(ax=ax, facecolor='red', edgecolor='red', alpha=0.4, linewidth=3, zorder=4)
                centroid = row.geometry.centroid
                ax.text(centroid.x, centroid.y, f"M{row.idx}", color='white', fontsize=12, fontweight='bold', ha='center', va='center', zorder=10, bbox=dict(facecolor='darkred', alpha=0.8, edgecolor='white', boxstyle='round,pad=0.2'))

        ax.set_xlim(-125, -65)
        ax.set_ylim(24, 50)
        ax.set_title(f"TCF Verification | VT: {valid_dt.strftime('%H:00Z')} | 5-Min Rolling Swath", color='white', fontsize=18, pad=15)
        ax.set_facecolor('black')
        ax.tick_params(colors='white')
        for spine in ax.spines.values(): spine.set_edgecolor('white')
        fig.patch.set_facecolor('black')

        legend_elements = [
            Patch(facecolor='none', edgecolor='lime', linewidth=3, label='Verified Well (>=50%)'),
            Patch(facecolor='none', edgecolor='yellow', linewidth=3, label='Verified Close (20-49%)'),
            Patch(facecolor='none', edgecolor='orange', linewidth=3, label='Overforecasted (<20%)'),
            Patch(facecolor='red', edgecolor='red', alpha=0.4, label='Missed'),
            Line2D([0], [0], color='#505050', lw=1, label='State Borders'),
            Line2D([0], [0], color='yellow', lw=1.5, linestyle=':', label='ARTCC Regions')
        ]
        plt.legend(handles=legend_elements, facecolor='black', labelcolor='white', loc='lower right')
        
        st.pyplot(fig)

    with col2:
        st.subheader("FAA Google Doc Report")
        
        doc_report = {"Verified Well:": [], "Verified Close:": [], "Over-forecast:": [], "Missed:": []}
        
        if not gdf_graded_fcst.empty:
            for _, row in gdf_graded_fcst.iterrows():
                artccs = get_artccs(row.geometry, gdf_artcc)
                top_str = f" [Top: {row.top:.1f} kft]" if row.top > 0 else ""
                line_text = f"{artccs} - Sparse (Area {row.idx}){top_str}"
                if row.category == "Verified Well": doc_report["Verified Well:"].append(line_text)
                elif row.category == "Verified Close": doc_report["Verified Close:"].append(line_text)
                elif row.category == "Overforecasted": doc_report["Over-forecast:"].append(line_text)

        if not gdf_graded_miss.empty:
            for _, row in gdf_graded_miss.iterrows():
                artccs = get_artccs(row.geometry, gdf_artcc)
                doc_report["Missed:"].append(f"{artccs} - Missed (Area M{row.idx})")

        report_text = f"National System Review\nNWS TCF Review\n{valid_dt.strftime('%A, %B %d, %Y')}\n"
        report_text += f"  {valid_dt.strftime('%b %d, %Y')}   IT: {issuance_hour:02d}Z   VT: {valid_dt.strftime('%H')}Z   FCST HR: {lead_time:02d}\n"
        report_text += "https://www.aviationweather.gov/tcf/help\nCollaboration: AWC, ZAB, ZAU, ZDC, ZDV, ZFW, ZHU, ZID, ZJX, ZKC, ZLC, ZMA, ZME, ZMP, ZOB, ZSE, ZTL\n\n"
        
        for cat, items in doc_report.items():
            report_text += f"{cat}\n"
            if not items: report_text += "None\n"
            for item in items: report_text += f"{item}\n"
            report_text += "\n"

        st.code(report_text, language="text")
        
        del max_tops, max_refl, top_verif_matrix
        gc.collect()
