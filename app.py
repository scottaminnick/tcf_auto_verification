import html
import io
import re
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
from matplotlib.path import Path  # still used for point-in-polygon in the echo-top calc
import plotly.graph_objects as go
import geopandas as gpd
from shapely.geometry import Polygon, LineString
from skimage import measure
import gc
from scipy.ndimage import uniform_filter, binary_dilation

# --- 1. PAGE CONFIG & CACHED LOADERS ---
st.set_page_config(page_title="TCF Verification Dashboard", layout="wide", page_icon="✈️")
st.title("Objective TCF Verification Dashboard")

# cache_resource keeps the big map files in memory across reruns (much safer than cache_data here)
@st.cache_resource
def load_geography():
    """Loads States and ARTCC boundaries once and keeps them in memory."""
    states = gpd.GeoDataFrame(geometry=[])
    artccs = gpd.GeoDataFrame(geometry=[])

    try:
        # Load States from the public internet, bypassing Fiona
        url = "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json"
        response = requests.get(url, timeout=10)
        states_data = response.json()
        states = gpd.GeoDataFrame.from_features(states_data["features"], crs="EPSG:4326")
    except Exception as e:
        st.sidebar.error(f"State boundaries error: {e}")

    try:
        # Read the local ARTCC file using pure Python, bypassing Fiona entirely
        import json
        with open("artcc1.geojson", "r", encoding="utf-8") as f:
            artcc_data = json.load(f)
        artccs = gpd.GeoDataFrame.from_features(artcc_data["features"], crs="EPSG:4326")
    except Exception as e:
        st.sidebar.error(f"❌ ARTCC Parsing Error: {e}")

    return states, artccs

# Load geography once. These stay available on every rerun, so the render
# functions below can reference them as globals (no need to stash in session_state).
gdf_states, gdf_artcc = load_geography()


# --- 2. HELPER FUNCTIONS ---
def _coverage_label(cov_val):
    """Map a TCF/CCFP coverage integer code to its plain-English label.
    TCF 1-digit encoding: 1=Dense (75%+), 2=Medium (40-74%), 3=Sparse (25-39%)."""
    if cov_val == 1:
        return "Dense"
    elif cov_val == 2:
        return "Medium"
    return "Sparse"


def parse_iem_cow_text(text_data):
    """Parses legacy NWS/AWIPS AREA/LINE text into a GeoDataFrame, fixing line-wraps with regex."""
    records = []

    # Strip ALL HTML tags so we just have raw text and numbers
    text_data = re.sub(r'<[^>]+>', ' ', text_data)

    # TCF/CCFP format:
    #   AREA: COV(0) CONF(1) GRW(2) TOPS(3) SPEED(4) DIR(5) NPTS(6) lat1 lon1 ...
    #   LINE: COV(0) NPTS(1) lat1 lon1 ...
    # COV is a 1-digit integer: 1=Dense, 2=Medium, 3=Sparse
    feat_blocks = re.findall(r'(AREA|LINE)\s+([\d\s]+)', text_data)

    for feat_type, block in feat_blocks:
        parts = block.split()
        try:
            cov_val = int(parts[0])
            if feat_type == 'LINE':
                # LINE has no CONF/GRW/TOPS/SPEED/DIR fields
                num_points = int(parts[1])
                idx = 2
            else:
                num_points = int(parts[6])
                idx = 7
            coords = []

            for _ in range(num_points):
                if idx + 1 < len(parts):
                    lat = float(parts[idx]) / 10.0
                    lon = float(parts[idx + 1]) / 10.0
                    if lon > 0:
                        lon = -lon
                    coords.append((lon, lat))
                    idx += 2

            if len(coords) >= 3:
                # CHANGED: .buffer(0) instead of .convex_hull.
                # buffer(0) repairs self-intersecting ("bowtie") polygons WITHOUT
                # filling in concave dents. convex_hull was inflating the forecast
                # area, which shrank the coverage fraction and under-graded good
                # forecasts (and hid real misses). This matches the notebook.
                poly = Polygon(coords).buffer(0)
                if not poly.is_empty:
                    records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})
            elif len(coords) >= 2:
                poly = LineString(coords).buffer(0.15)
                records.append({'geometry': poly, 'coverage': cov_val, 'feat_type': feat_type})

        except Exception:
            continue

    if records:
        return gpd.GeoDataFrame(records, crs="EPSG:4326")
    else:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def fetch_iem_cow_tcf(date_obj, issue_hr, f_hr):
    """Automatically scrapes the TCF text from IEM archives."""
    date_str = date_obj.strftime("%Y%m%d")
    issue_str = f"{issue_hr:02d}"

    # CHANGED: TCF products are valid at 4/6/8 hrs after issuance. Verified from the
    # product header (CCFP issued_1300 valid_1700 == 4hr lead == PIL "CFP02"). The old
    # mapping was shifted one slot low and pulled the wrong valid time.
    if f_hr == 4:
        pil = "CFP02"
    elif f_hr == 6:
        pil = "CFP03"
    elif f_hr == 8:
        pil = "CFP04"
    else:
        pil = "CFP02"

    url = f"https://mesonet.agron.iastate.edu/wx/afos/p.php?pil={pil}&e={date_str}{issue_str}00"

    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            if "Could not find product" in response.text:
                st.sidebar.error(f"IEM: Data missing for {issue_str}:00Z ({pil})")
                return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

            return parse_iem_cow_text(response.text)
    except Exception as e:
        st.sidebar.error(f"IEM Fetch Error: {e}")

    return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def get_artccs(poly, artcc_gdf):
    """Finds which ARTCCs a polygon intersects."""
    if artcc_gdf.empty:
        return "UNKNOWN"
    intersecting = artcc_gdf[artcc_gdf.intersects(poly)]
    if intersecting.empty:
        return "UNKNOWN"

    if 'IDENT' in intersecting.columns:
        centers = intersecting['IDENT'].dropna().unique().tolist()
    else:
        centers = ["UNKNOWN_COL"]
    return "/".join(centers)


def download_mrms_scan(product, dt_obj, dest_dir="mrms_data"):
    os.makedirs(dest_dir, exist_ok=True)
    date_str = dt_obj.strftime('%Y%m%d')
    bucket_name = 'noaa-mrms-pds'
    prefix = f"CONUS/{product}_00.50/{date_str}/"
    s3 = boto3.client('s3', config=botocore.client.Config(signature_version=botocore.UNSIGNED))

    try:
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        if 'Contents' not in response:
            return None

        # CHANGED: pick the file NEAREST in time to dt_obj, not an exact HHMM match.
        # MRMS scans are issued ~every 2 min at timestamps like ...20260524-231038
        # (note the seconds), so requests on 5-min marks rarely have an exact match.
        # The old exact-match returned None and silently dropped that scan from the
        # rolling composite -- which is why only ~3 of 7 scans were being used.
        best_key, best_diff = None, None
        for obj in response['Contents']:
            key = obj['Key']
            if not key.endswith('.grib2.gz'):
                continue
            m = re.search(r'(\d{8})-(\d{6})', key.split('/')[-1])
            if not m:
                continue
            file_dt = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
            diff = abs((file_dt - dt_obj).total_seconds())
            if best_diff is None or diff < best_diff:
                best_key, best_diff = key, diff

        # Reject if the closest file is more than 5 min away (a genuine archive gap).
        if best_key is None or best_diff > 5 * 60:
            return None

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


def extract_tcf_polygons(coverage_mask, lons, lats, min_area_m2=0):
    """Turns a binary coverage mask into dissolved 'truth' polygons."""
    contours = measure.find_contours(coverage_mask, 0.5)
    polygons = []
    for contour in contours:
        if len(contour) > 10:
            poly = Polygon(zip([lons[int(p[1])] for p in contour],
                               [lats[int(p[0])] for p in contour]))
            if poly.is_valid:
                polygons.append(poly.simplify(0.05))
    gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    if gdf.is_empty.all():
        return gdf

    gdf_m = gdf.to_crs("EPSG:5070")
    if min_area_m2 > 0:
        valid_area = gdf_m.geometry.area >= min_area_m2
        gdf = gdf[valid_area]
    if not gdf.is_empty.all():
        # CHANGED: .union_all() (modern GeoPandas API) replaces the deprecated
        # .unary_union, matching the notebook.
        gdf = gpd.GeoDataFrame(geometry=[gdf.union_all()], crs="EPSG:4326")
    return gdf


# --- 3. RENDER FUNCTIONS ---------------------------------------------------
# These read already-computed results out of session_state and draw a figure.
# They run on EVERY rerun (e.g. when the view radio is toggled), which is why
# the heavy computation must NOT live here.

# Discrete echo-top color scale (z=0 is set to NaN before plotting, so it stays transparent).
# Boundaries are normalized z/4: 1->cyan, 2->yellow, 3->orange, 4->red.
ECHO_COLORSCALE = [
    [0.0, '#000000'], [0.2, '#000000'],
    [0.2, '#00FFFF'], [0.4, '#00FFFF'],
    [0.4, '#FFFF00'], [0.6, '#FFFF00'],
    [0.6, '#FF8000'], [0.8, '#FF8000'],
    [0.8, '#FF0000'], [1.0, '#FF0000'],
]


def _geom_to_xy(geom):
    """Flatten a shapely Polygon/MultiPolygon exterior(s) to x,y lists with None breaks
    (None tells Plotly to lift the pen between separate rings)."""
    xs, ys = [], []
    if geom is None or geom.is_empty:
        return xs, ys
    polys = geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]
    for p in polys:
        x, y = p.exterior.xy
        xs.extend(list(x) + [None])
        ys.extend(list(y) + [None])
    return xs, ys


def _gdf_to_xy(gdf):
    """Flatten an entire GeoDataFrame of polygons into one set of x,y line coords."""
    xs, ys = [], []
    for geom in gdf.geometry:
        gx, gy = _geom_to_xy(geom)
        xs.extend(gx)
        ys.extend(gy)
    return xs, ys


def _new_map_fig(R, title):
    """Build the shared interactive base map: radar echo-top heatmap + state + ARTCC borders.
    Everything is drawn from our own arrays/geometry -- no external map tiles, so this is
    safe on a locked-down network."""
    fig = go.Figure()

    # Radar background. 0 (no convection) -> NaN so those cells render transparent.
    z = np.where(R['top_verif_matrix'] == 0, np.nan, R['top_verif_matrix'].astype(float))
    fig.add_trace(go.Heatmap(
        x=R['lons'], y=R['lats'], z=z,
        colorscale=ECHO_COLORSCALE, zmin=0, zmax=4,
        showscale=False, hoverinfo='skip', name='Echo Tops'))

    sx, sy = _gdf_to_xy(gdf_states)
    if sx:
        fig.add_trace(go.Scatter(x=sx, y=sy, mode='lines', name='State Borders',
                                 line=dict(color='#777777', width=1), hoverinfo='skip'))

    ax_, ay_ = _gdf_to_xy(gdf_artcc)
    if ax_:
        fig.add_trace(go.Scatter(x=ax_, y=ay_, mode='lines', name='ARTCC Regions',
                                 line=dict(color='yellow', width=1.2, dash='dot'), hoverinfo='skip'))

    # scaleratio ~1.25 corrects the lon/lat aspect near mid-CONUS (1/cos(37 deg)) so the
    # map isn't horizontally stretched. Zoom/pan/box-zoom come for free from Plotly.
    fig.update_layout(
        title=dict(text=title, font=dict(color='white', size=18)),
        template='plotly_dark', paper_bgcolor='black', plot_bgcolor='black',
        xaxis=dict(range=[-125, -65], showgrid=False, zeroline=False, color='white'),
        yaxis=dict(range=[24, 50], showgrid=False, zeroline=False, color='white',
                   scaleanchor='x', scaleratio=1.25),
        legend=dict(bgcolor='rgba(0,0,0,0.5)', font=dict(color='white'),
                    x=0.99, y=0.01, xanchor='right', yanchor='bottom'),
        margin=dict(l=10, r=10, t=50, b=10), height=650)
    return fig


def render_scorecard(R):
    """View 1: graded forecast polygons + misses (interactive), plus the FAA text report."""
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Objective Verification Scorecard Map")
        fig = _new_map_fig(R, f"TCF Verification | VT: {R['valid_dt'].strftime('%H:00Z')} | 5-Min Rolling Swath")

        gf, gm = R['gdf_graded_fcst'], R['gdf_graded_miss']
        label_x, label_y, label_txt = [], [], []
        seen = set()  # only show each grade once in the legend

        if not gf.empty:
            for _, row in gf.iterrows():
                xs, ys = _geom_to_xy(row.geometry)
                show = row.category not in seen
                seen.add(row.category)
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode='lines', line=dict(color=row.color, width=3),
                    name=row.category, legendgroup=row.category, showlegend=show,
                    hovertemplate=f"Area {row.idx} — {row.category}<br>Top: {row.top:.1f} kft<extra></extra>"))
                c = row.geometry.centroid
                label_x.append(c.x); label_y.append(c.y); label_txt.append(str(row.idx))

        if not gm.empty:
            show = True
            for _, row in gm.iterrows():
                xs, ys = _geom_to_xy(row.geometry)
                fig.add_trace(go.Scatter(
                    x=xs, y=ys, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.35)',
                    line=dict(color='red', width=2), name='Missed', legendgroup='Missed',
                    showlegend=show, hovertemplate=f"Missed Area M{row.idx}<extra></extra>"))
                show = False
                c = row.geometry.centroid
                label_x.append(c.x); label_y.append(c.y); label_txt.append(f"M{row.idx}")

        if label_txt:
            fig.add_trace(go.Scatter(x=label_x, y=label_y, mode='text', text=label_txt,
                                     textfont=dict(color='white', size=13, family='Arial Black'),
                                     hoverinfo='skip', showlegend=False))

        st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True})

    with col2:
        st.subheader("FAA Google Doc Report")
        escaped = html.escape(R['report_text'])
        st.markdown(
            f'<div style="font-family: Calibri, sans-serif; font-size: 24px; '
            f'background-color: white; color: black; padding: 12px; '
            f'white-space: pre-wrap; overflow-x: auto; border-radius: 4px;">'
            f'{escaped}</div>',
            unsafe_allow_html=True
        )


def render_reanalysis(R):
    """View 2: the objective 'truth' -- what the TCF should have been (sparse reanalysis)."""
    st.subheader("Objective TCF Reanalysis (Ground Truth)")
    st.caption("30-min rolling composite, 25% coverage rule. Cyan dashed = objective sparse areas.")

    fig = _new_map_fig(R, f"Objective TCF Reanalysis (Truth) | VT: {R['valid_dt'].strftime('%H:00Z')}")

    gs = R['gdf_sparse']
    if not gs.is_empty.all():
        xs, ys = _gdf_to_xy(gs)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode='lines', name='Sparse Reanalysis (25%+)',
                                 line=dict(color='cyan', width=3, dash='dash'),
                                 hovertemplate="Objective truth area<extra></extra>"))

    st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True})


def build_report(gdf_graded_fcst, gdf_graded_miss, valid_dt, issuance_hour, lead_time):
    """Assembles the copy-paste FAA/NWS text report."""
    report_text = ""
    doc_report = {"Verified Well:": [], "Verified Close:": [], "Over-forecast:": [], "Missed:": []}

    if not gdf_graded_fcst.empty:
        for _, row in gdf_graded_fcst.iterrows():
            artccs = get_artccs(row.geometry, gdf_artcc)
            top_str = f" [Top: {row.top:.1f} kft]" if row.top > 0 else ""
            cov_label = _coverage_label(getattr(row, 'coverage', 25))
            feat_label = "Line" if getattr(row, 'feat_type', 'AREA') == 'LINE' else "Area"
            line_text = f"{artccs} - {cov_label} ({feat_label} {row.idx}){top_str}"
            if row.category == "Verified Well":
                doc_report["Verified Well:"].append(line_text)
            elif row.category == "Verified Close":
                doc_report["Verified Close:"].append(line_text)
            elif row.category == "Overforecasted":
                doc_report["Over-forecast:"].append(line_text)

    if not gdf_graded_miss.empty:
        for _, row in gdf_graded_miss.iterrows():
            artccs = get_artccs(row.geometry, gdf_artcc)
            doc_report["Missed:"].append(f"{artccs} - Missed (Area M{row.idx})")

    for cat, items in doc_report.items():
        report_text += f"{cat}\n"
        if not items:
            report_text += "None\n"
        for item in items:
            report_text += f"{item}\n"
        report_text += "\n"
    return report_text


# --- 4. SIDEBAR CONTROLS ---
st.sidebar.header("Event Selection")
target_date = st.sidebar.date_input("Select Event Date", datetime(2026, 5, 24))
issuance_hour = st.sidebar.selectbox("Issuance Time (Z)", [5, 7, 9, 11, 13, 15, 17, 19, 21, 23], index=7)
lead_time = st.sidebar.radio("Forecast Hour", [4, 6, 8])

valid_time = issuance_hour + lead_time
if valid_time >= 24:
    valid_time -= 24
    valid_dt = datetime.combine(target_date + timedelta(days=1), time(valid_time, 0))
else:
    valid_dt = datetime.combine(target_date, time(valid_time, 0))

st.sidebar.markdown(f"**Valid Time (VT):** {valid_dt.strftime('%b %d, %H:00Z')}")


# --- 5. MAIN EXECUTION (compute once, then stash in session_state) ---
if st.sidebar.button("Run Verification"):

    with st.status("Fetching Data...", expanded=True) as status:
        # AUTOMATIC FETCH VIA IEM
        st.write("Pulling Forecast from IEM Archives...")
        gdf_forecast = fetch_iem_cow_tcf(target_date, issuance_hour, lead_time)

        if gdf_forecast.empty:
            st.warning("IEM failed or data missing for this issuance/lead time.")
            st.stop()

        # --- Rolling Composite ---
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

    # --- Verification Math ---
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

        # CHANGED: 15_000_000_000 (15,000 km^2) truth-area filter to match the notebook
        # (was 10_000_000_000). Larger filter = same set of 'truth' blobs the notebook grades against.
        gdf_sparse = extract_tcf_polygons((coverage_fraction >= 0.25).astype(int), lons, lats,
                                          min_area_m2=15_000_000_000)
        # Medium (cov=2) and Dense (cov=1) forecasts must verify against 40%+ truth,
        # matching the TCF Medium coverage threshold (40-74%).
        gdf_medium_truth = extract_tcf_polygons((coverage_fraction >= 0.40).astype(int), lons, lats,
                                                min_area_m2=15_000_000_000)
        del coverage_fraction, raw_cores, buffered_cores
        gc.collect()

        # CHANGED: .union_all() instead of deprecated .unary_union (two places)
        truth_sparse_union = gdf_sparse.union_all() if not gdf_sparse.is_empty.all() else Polygon()
        truth_medium_union = gdf_medium_truth.union_all() if not gdf_medium_truth.is_empty.all() else Polygon()
        fcst_union = gdf_forecast.union_all() if not gdf_forecast.is_empty.all() else Polygon()

        graded_forecasts, graded_misses = [], []

        fcst_iter = (gdf_forecast.explode(index_parts=False).reset_index(drop=True)
                     if not gdf_forecast.is_empty.all() else gpd.GeoDataFrame(geometry=[]))
        for idx, row in fcst_iter.iterrows():
            poly = row.geometry
            if poly.is_empty:
                continue

            row_cov = row['coverage'] if 'coverage' in fcst_iter.columns else 3
            # Sparse (3) forecasts verify against 25%+ truth; Medium/Dense (1,2) against 40%+ truth.
            truth_union = truth_sparse_union if row_cov == 3 else truth_medium_union

            fcst_area = poly.area
            hit_area = poly.intersection(truth_union).area
            coverage = hit_area / fcst_area if fcst_area > 0 else 0

            min_lon, min_lat, max_lon, max_lat = poly.bounds
            lat_mask, lon_mask = (lats >= min_lat) & (lats <= max_lat), (lons >= min_lon) & (lons <= max_lon)
            subset_tops, subset_refl = max_tops[lat_mask][:, lon_mask], max_refl[lat_mask][:, lon_mask]
            lon_grid, lat_grid = np.meshgrid(lons[lon_mask], lats[lat_mask])

            in_poly_mask = Path(np.array(poly.exterior.coords)).contains_points(
                np.vstack((lon_grid.flatten(), lat_grid.flatten())).T).reshape(lon_grid.shape)
            valid_tops = subset_tops[in_poly_mask & (subset_refl >= 40) & (subset_tops >= 25)]

            actual_top_kft = np.percentile(valid_tops, 90) if len(valid_tops) > 5 else 0

            cat, color = ("Verified Well", 'lime') if coverage >= 0.50 else \
                         ("Verified Close", 'yellow') if coverage >= 0.20 else \
                         ("Overforecasted", 'orange')
            row_feat = row['feat_type'] if 'feat_type' in fcst_iter.columns else 'AREA'
            graded_forecasts.append({'geometry': poly, 'category': cat, 'color': color,
                                     'idx': idx + 1, 'top': actual_top_kft,
                                     'coverage': row_cov, 'feat_type': row_feat})

        truth_iter = (gdf_sparse.explode(index_parts=False).reset_index(drop=True)
                      if not gdf_sparse.is_empty.all() else gpd.GeoDataFrame(geometry=[]))
        for idx, row in truth_iter.iterrows():
            poly = row.geometry
            if poly.is_empty:
                continue
            captured = (poly.intersection(fcst_union).area / poly.area) if poly.area > 0 else 0
            if captured < 0.20:
                graded_misses.append({'geometry': poly, 'category': 'Missed', 'color': 'red', 'idx': idx + 1})

        # ORDER EAST -> WEST: east = larger (least-negative) longitude, so sort centroid.x
        # descending. Renumber after sorting so BOTH the map labels and the report read E->W.
        # Report stays grouped by grade (build_report buckets by category); because we iterate
        # this E->W-sorted list, each grade group ends up E->W internally.
        graded_forecasts.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
        for i, r in enumerate(graded_forecasts, start=1):
            r['idx'] = i
        graded_misses.sort(key=lambda r: r['geometry'].centroid.x, reverse=True)
        for i, r in enumerate(graded_misses, start=1):
            r['idx'] = i

        gdf_graded_fcst = gpd.GeoDataFrame(graded_forecasts, crs="EPSG:4326") if graded_forecasts else gpd.GeoDataFrame(geometry=[])
        gdf_graded_miss = gpd.GeoDataFrame(graded_misses, crs="EPSG:4326") if graded_misses else gpd.GeoDataFrame(geometry=[])

        report_out = build_report(gdf_graded_fcst, gdf_graded_miss, valid_dt, issuance_hour, lead_time)

        # max_tops / max_refl no longer needed; keep top_verif_matrix for plotting
        del max_tops, max_refl
        gc.collect()

    # STASH everything the render functions need so it survives reruns (radio toggles).
    st.session_state['results'] = {
        'lons': lons, 'lats': lats,
        'top_verif_matrix': top_verif_matrix,
        'gdf_graded_fcst': gdf_graded_fcst,
        'gdf_graded_miss': gdf_graded_miss,
        'gdf_sparse': gdf_sparse,
        'report_text': report_out,
        'valid_dt': valid_dt,
    }


# --- 6. VIEW SWITCHER (runs every rerun; reads from session_state) ---
if 'results' in st.session_state:
    st.markdown("---")
    view = st.radio("Select View", ["Verification Scorecard", "Reanalysis (Truth)"],
                    horizontal=True)
    R = st.session_state['results']
    if view == "Verification Scorecard":
        render_scorecard(R)
    else:
        render_reanalysis(R)
else:
    st.info("Set the event in the sidebar and click **Run Verification** to begin.")
