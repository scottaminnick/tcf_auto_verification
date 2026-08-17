import base64
import gc
import html
import io
import os
import streamlit as st
import requests
from datetime import date, datetime, timezone
import numpy as np
import plotly.graph_objects as go
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")          # headless: no display, no GUI backend
import matplotlib.image as mimage

# The pipeline (parsing, MRMS composite, verification math, report text) lives in
# tcf_pipeline.py and is shared verbatim with baseline/capture.py. This file owns
# the Streamlit layer only: caching, progress, layout and plotting. Anything that
# changes a number belongs in tcf_pipeline.py, so that the baseline harness --
# which replays tcf_pipeline against frozen inputs -- actually covers it.
import tcf_pipeline
from tcf_pipeline import (
    build_composite,
    compute_valid_dt,
    fetch_iem_cow_tcf,
    run_verification,
)

# --- 1. PAGE CONFIG & CACHED LOADERS ---
st.set_page_config(page_title="TCF Verification Dashboard", layout="wide", page_icon="✈️")

# Password gate — set APP_PASSWORD in Railway environment variables.
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if _APP_PASSWORD:
    if not st.session_state.get("authenticated"):
        st.title("TCF Verification Dashboard")
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == _APP_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

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
        artccs = tcf_pipeline.load_artccs()
    except Exception as e:
        st.sidebar.error(f"❌ ARTCC Parsing Error: {e}")

    return states, artccs


@st.cache_data(show_spinner=False)
def cached_fetch_iem_cow_tcf(date_obj, issue_hr, f_hr):
    """Cached wrapper around the pipeline's IEM fetch.

    tcf_pipeline.fetch_iem_cow_tcf() raises on a missing product or a bad HTTP
    status; the dashboard's long-standing behaviour is a sidebar error plus an
    empty frame (the caller then warns and stops), so the exception is turned
    back into exactly that here.
    """
    try:
        return fetch_iem_cow_tcf(date_obj, issue_hr, f_hr)
    except Exception as e:
        st.sidebar.error(f"IEM Fetch Error: {e}")
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

@st.cache_data(show_spinner=False)
def cached_build_composite(valid_dt, window_minutes, cadence_minutes, step, _log=None):
    """Cached wrapper around the pipeline's MRMS composite.

    Keyed on the scalar arguments only. `_log` is underscore-prefixed so
    Streamlit leaves it out of the cache key -- it is a callback, it has no
    bearing on the arrays, and hashing it would defeat the cache on every rerun.
    A cache hit therefore returns the composite without re-emitting the per-scan
    progress lines, which is the point: the whole fetch is skipped.

    The wrapper lives here rather than in tcf_pipeline, which must stay
    streamlit-free.
    """
    return build_composite(valid_dt, log=_log or (lambda _msg: None),
                           window_minutes=window_minutes,
                           cadence_minutes=cadence_minutes,
                           step=step,
                           with_display=True)


@st.cache_data(show_spinner=False, max_entries=3)
def cached_display_png(valid_dt, window_minutes, cadence_minutes, _raster=None):
    """Cached PNG of the display raster, keyed separately from the composite.

    Its own cache entry rather than a field on the composite's: the PNG is the
    only large thing the browser ever sees, and colouring it is pure display
    work. Re-rendering it (a palette change) must not evict or invalidate the
    verification arrays, and vice versa. max_entries is small because each entry
    holds a full-resolution image.

    `_raster` is underscore-prefixed so streamlit does not try to hash a pair of
    24.5M-point arrays on every rerun -- the scalars above already identify it.
    """
    png, factor = display_png(_raster.max_tops, _raster.max_refl)
    return png, _raster.extent, factor


# Load geography once. These stay available on every rerun, so the render
# functions below can reference them as globals (no need to stash in session_state).
gdf_states, gdf_artcc = load_geography()



# --- 2. HELPER FUNCTIONS ---
# Moved to tcf_pipeline.py, imported at the top of this file: _coverage_label,
# parse_iem_cow_text, fetch_iem_cow_tcf, get_artccs, download_mrms_scan,
# extract_tcf_polygons, build_report, and the compute_valid_dt /
# build_composite / run_verification split. They were moved verbatim -- if one
# needs to change, change it there so baseline/check.py covers the change.

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

# Echo-top bands for cells at or above CONVECTIVE_DBZ, coloured as on the AWC
# product: (lower bound in kft, colour, label). The <25 kft band is drawn now --
# previously anything under 25 kft was simply invisible, which made weak-topped
# convection look like no convection at all.
CONVECTIVE_DBZ = 40        # at/above this the cell is coloured by echo top
ECHO_BANDS = [
    (0,  '#3D5AA8', '<25 kft'),
    (25, '#00FFFF', '25-30 kft'),
    (30, '#FFFF00', '30-35 kft'),
    (35, '#FF8000', '35-40 kft'),
    (40, '#FF0000', '>40 kft'),
]

# Below CONVECTIVE_DBZ the cell is drawn as plain reflectivity on a grey ramp,
# so stratiform rain reads as context rather than competing with the convective
# colours. Anything under GREY_MIN_DBZ is left fully transparent.
GREY_MIN_DBZ = 5
GREY_MAX_DBZ = 40
GREY_DARK, GREY_LIGHT = 0.28, 0.86


def _hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def display_rgba(max_tops, max_refl):
    """Colour the display raster the way the AWC product does.

    Greyscale ramp for reflectivity below CONVECTIVE_DBZ, echo-top band colours
    at or above it, transparent where there is nothing to show. Returns uint8
    RGBA, built with numpy rather than a matplotlib colormap so the band edges
    land exactly on the thresholds instead of on colormap interpolation.
    """
    rgba = np.zeros(max_refl.shape + (4,), dtype=np.uint8)

    weak = (max_refl >= GREY_MIN_DBZ) & (max_refl < CONVECTIVE_DBZ)
    if weak.any():
        frac = np.clip((max_refl[weak] - GREY_MIN_DBZ) / (GREY_MAX_DBZ - GREY_MIN_DBZ), 0, 1)
        grey = ((GREY_DARK + frac * (GREY_LIGHT - GREY_DARK)) * 255).astype(np.uint8)
        rgba[weak, 0] = rgba[weak, 1] = rgba[weak, 2] = grey
        rgba[weak, 3] = 255

    convective = max_refl >= CONVECTIVE_DBZ
    for lower, color, _label in ECHO_BANDS:
        upper = next((b[0] for b in ECHO_BANDS if b[0] > lower), None)
        band = convective & (max_tops >= lower)
        if upper is not None:
            band &= max_tops < upper
        if not band.any():
            continue
        r, g, b = _hex_to_rgb(color)
        rgba[band, 0], rgba[band, 1], rgba[band, 2] = r, g, b
        rgba[band, 3] = 255

    return rgba


def display_png(max_tops, max_refl, max_width=3500):
    """RGBA raster -> in-memory PNG bytes.

    Downsampled to max_width because the native grid is 7000 px across and a
    browser gains nothing from more pixels than the map is ever drawn at; the
    verification grid is untouched either way.
    """
    factor = max(1, int(np.ceil(max_refl.shape[1] / max_width)))
    rgba = display_rgba(max_tops[::factor, ::factor], max_refl[::factor, ::factor])
    buf = io.BytesIO()
    mimage.imsave(buf, rgba, format="png")
    return buf.getvalue(), factor


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

    # Radar background as a STATIC IMAGE, not a heatmap trace.
    #
    # The heatmap shipped one z value per grid cell to the browser -- ~7 MB of
    # JSON/binary for a picture that never changes once drawn. Rendering the same
    # raster to a PNG server-side and anchoring it to the lon/lat extent sends a
    # few hundred KB instead, and lets the display raster stay at native 0.01 deg
    # resolution rather than being decimated to keep the payload sane.
    #
    # Everything else -- polygons, borders, labels, the legend -- stays vector on
    # top, so hover, zoom and legend toggling still work on those.
    if R.get('display_png'):
        lon_min, lon_max, lat_min, lat_max = R['display_extent']
        fig.add_layout_image(dict(
            source="data:image/png;base64," + base64.b64encode(R['display_png']).decode(),
            xref="x", yref="y",
            x=lon_min, y=lat_max,                    # anchored top-left
            sizex=lon_max - lon_min, sizey=lat_max - lat_min,
            xanchor="left", yanchor="top",
            sizing="stretch", layer="below", opacity=1.0))

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
    # dragmode='pan' makes drag pan instead of box-zoom; scroll still zooms.
    fig.update_layout(
        dragmode='pan',
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

        st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displaylogo': False})

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
        st.download_button("Pass A", R['report_text'], file_name="pass_a_report.txt")

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

    st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displaylogo': False})


# --- 4. SIDEBAR CONTROLS ---
# Earliest selectable event. MRMS v12 went operational on 2020-10-15; the
# archive before that is a different product version, so the echo tops would not
# be comparable even where the S3 keys exist.
MRMS_V12_START = date(2020, 10, 15)

st.sidebar.header("Event Selection")
# UTC, not local. A forecaster working 2100 CDT is already on the next UTC day,
# and every date this app handles -- TCF issuance hours, MRMS S3 key prefixes --
# is UTC, so a local "today" would silently offer the wrong day.
_today_utc = datetime.now(timezone.utc).date()
target_date = st.sidebar.date_input(
    "Select Event Date", value=_today_utc,
    min_value=MRMS_V12_START, max_value=_today_utc)
issuance_hour = st.sidebar.selectbox("Issuance Time (Z)", [5, 7, 9, 11, 13, 15, 17, 19, 21, 23], index=7)
lead_time = st.sidebar.radio("Forecast Hour", [4, 6, 8])

valid_dt = compute_valid_dt(target_date, issuance_hour, lead_time)

st.sidebar.markdown(f"**Valid Time (VT):** {valid_dt.strftime('%b %d, %H:00Z')}")


# --- 5. MAIN EXECUTION (compute once, then stash in session_state) ---
if st.sidebar.button("Run Verification"):

    with st.status("Fetching Data...", expanded=True) as status:
        # AUTOMATIC FETCH VIA IEM
        st.write("Pulling Forecast from IEM Archives...")
        gdf_forecast = cached_fetch_iem_cow_tcf(target_date, issuance_hour, lead_time)

        if gdf_forecast.empty:
            st.warning("IEM failed or data missing for this issuance/lead time.")
            st.stop()

        # --- Rolling Composite ---
        # st.write is handed to the pipeline as its progress sink, so the per-scan
        # lines still appear in this status box.
        max_tops, max_refl, lons, lats, raster = cached_build_composite(
            valid_dt,
            tcf_pipeline.COMPOSITE_WINDOW_MINUTES,
            tcf_pipeline.COMPOSITE_CADENCE_MINUTES,
            tcf_pipeline.COMPOSITE_STEP,
            _log=st.write)

        st.write("Rendering display raster...")
        display_png_bytes, display_extent, _factor = cached_display_png(
            valid_dt,
            tcf_pipeline.COMPOSITE_WINDOW_MINUTES,
            tcf_pipeline.COMPOSITE_CADENCE_MINUTES,
            _raster=raster)

        st.write("Building Objective Truth Polygons...")
        status.update(label="Data processing complete!", state="complete", expanded=False)

    # --- Verification Math ---
    with st.spinner("Calculating Spatial Overlap & Echo Tops..."):
        R = run_verification(gdf_forecast, max_tops, max_refl, lons, lats,
                             valid_dt, issuance_hour, lead_time, gdf_artcc)

        # max_tops / max_refl no longer needed; keep top_verif_matrix for plotting
        del max_tops, max_refl
        gc.collect()

    # STASH everything the render functions need so it survives reruns (radio toggles).
    st.session_state['results'] = {
        'display_png': display_png_bytes,
        'display_extent': display_extent,
        'lons': R['lons'], 'lats': R['lats'],
        'top_verif_matrix': R['top_verif_matrix'],
        'gdf_graded_fcst': R['gdf_graded_fcst'],
        'gdf_graded_miss': R['gdf_graded_miss'],
        'gdf_sparse': R['gdf_sparse'],
        'report_text': R['report_text'],
        'valid_dt': R['valid_dt'],
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
