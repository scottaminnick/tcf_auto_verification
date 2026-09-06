"""Streamlit workstation layout for objective TCF verification.

The verification methodology stays in ``tcf_pipeline.py``.  This module only
reorganizes reviewer-facing presentation and adds the participant utility.
"""

from __future__ import annotations

import html
import json

import numpy as np
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import tcf_participants


PLOT_CONFIG = {"scrollZoom": True, "displaylogo": False}
REVIEW_RESULTS = ["Verified Well", "Verified Close", "Overforecast", "Missed"]


def _scorecard_figure(R, *, pipeline, new_map_fig, geom_to_xy, composite_label):
    fig = new_map_fig(
        R,
        f"TCF Verification | VT: {R['valid_dt'].strftime('%H:00Z')} | {composite_label()}",
    )

    forecasts = R["gdf_graded_fcst"]
    misses = R["gdf_graded_miss"]
    label_x, label_y, label_text = [], [], []
    seen = set()

    if not forecasts.empty:
        for _, row in forecasts.iterrows():
            xs, ys = geom_to_xy(row.geometry)
            show = row.category not in seen
            seen.add(row.category)
            top_text = (
                f"{row.top:.1f} kft"
                if row.top is not None and not np.isnan(row.top)
                else "Unavailable"
            )
            coverage_label = pipeline._coverage_label(row.feat_type, row.coverage)
            feature_label = "Line" if row.feat_type == "LINE" else "Area"
            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=row.color, width=3),
                name=row.category,
                legendgroup=row.category,
                showlegend=show,
                hovertemplate=(
                    f"{coverage_label} {feature_label} {row.idx} — {row.category}"
                    f"<br>Top: {top_text}<extra></extra>"
                ),
            ))
            centroid = row.geometry.centroid
            label_x.append(centroid.x)
            label_y.append(centroid.y)
            label_text.append(str(row.idx))

    if not misses.empty:
        show = True
        for _, row in misses.iterrows():
            xs, ys = geom_to_xy(row.geometry)
            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                fill="toself",
                fillcolor="rgba(255,0,0,0.35)",
                line=dict(color="red", width=2),
                name="Candidate Miss",
                legendgroup="Candidate Miss",
                showlegend=show,
                hovertemplate=(
                    f"Candidate Miss M{row.idx}<br>"
                    f"Sparse area: {row.sparse_area_km2:,.1f} km²<br>"
                    f"Forecast capture: {row.forecast_capture_fraction:.1%}<br>"
                    f"Medium core: {row.medium_core_area_km2:,.1f} km² "
                    f"({row.medium_core_fraction:.1%})<extra></extra>"
                ),
            ))
            show = False
            centroid = row.geometry.centroid
            label_x.append(centroid.x)
            label_y.append(centroid.y)
            label_text.append(f"M{row.idx}")

    medium_flags = R["gdf_medium_core_flags"]
    if not medium_flags.empty:
        show = True
        for _, row in medium_flags.iterrows():
            xs, ys = geom_to_xy(row.geometry)
            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                fill="toself",
                fillcolor="rgba(200,0,255,0.16)",
                line=dict(color="magenta", width=2, dash="dash"),
                name="Medium-core Review Flag",
                legendgroup="Medium-core Review Flag",
                showlegend=show,
                hovertemplate=(
                    f"Medium-core Review F{row.idx} (review cue only)<br>"
                    f"Medium area: {row.medium_area_km2:,.1f} km²<br>"
                    f"Forecast capture: {row.medium_capture_fraction:.1%}<br>"
                    f"Parent Sparse component: {row.parent_sparse_component_id}"
                    "<extra></extra>"
                ),
            ))
            show = False
            centroid = row.geometry.centroid
            label_x.append(centroid.x)
            label_y.append(centroid.y)
            label_text.append(f"F{row.idx}")

    if label_text:
        fig.add_trace(go.Scatter(
            x=label_x,
            y=label_y,
            mode="text",
            text=label_text,
            textfont=dict(color="white", size=13, family="Arial Black"),
            hoverinfo="skip",
            showlegend=False,
        ))

    return fig


def _reanalysis_figure(R, *, new_map_fig, gdf_to_xy):
    fig = new_map_fig(
        R,
        f"Objective TCF Reanalysis (Truth) | VT: {R['valid_dt'].strftime('%H:00Z')}",
    )
    layers = (
        ("gdf_pair_first_seed", "Decision 1A Pair-first Seed", "#FFFFFF", "dot", 1),
        ("gdf_dilated_seed", "Post-dilation Seed", "#FF00FF", "dashdot", 2),
        ("gdf_sparse", "Sparse Processed Truth (25%+)", "#00FFFF", "dash", 3),
        ("gdf_medium_truth", "Medium Processed Truth (40%+)", "#FFD700", "solid", 3),
    )
    for key, label, color, dash, width in layers:
        geometry = R[key]
        if not geometry.empty and not geometry.is_empty.all():
            xs, ys = gdf_to_xy(geometry)
            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=label,
                line=dict(color=color, width=width, dash=dash),
                hovertemplate=f"{label}<extra></extra>",
            ))

    forecasts = R["gdf_graded_fcst"]
    if not forecasts.empty and not forecasts.is_empty.all():
        xs, ys = gdf_to_xy(forecasts)
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            name="Issued TCF Forecasts",
            line=dict(color="#808080", width=1, dash="solid"),
            hovertemplate="Issued forecast geometry<extra></extra>",
        ))
    return fig


def _mrms_grid_text(provenance):
    excluded_grid = any(
        record.grid_compatible is False for record in provenance.observations
    )
    if excluded_grid:
        return "⚠ excluded mismatch"
    if provenance.all_used_grids_compatible:
        return "✓"
    return "⚠ no usable grid"


def _render_mrms_summary(provenance, *, duration):
    if provenance is None:
        return

    largest_offset = max(
        (
            value
            for value in (
                provenance.max_reflectivity_offset_seconds,
                provenance.max_echo_top_offset_seconds,
            )
            if value is not None
        ),
        default=None,
    )
    complete = provenance.observations_used == provenance.total_requested
    paired_status = "✓" if complete else "⚠"
    st.caption(
        f"MRMS observations: {paired_status} "
        f"{provenance.observations_used}/{provenance.total_requested} paired · "
        f"Max scan offset {duration(largest_offset)} · "
        f"Max pair separation {duration(provenance.max_product_separation_seconds)} · "
        f"Grid {_mrms_grid_text(provenance)}"
    )

    with st.expander("MRMS data details"):
        st.caption(
            f"Requested times: {provenance.total_requested} · "
            f"Reflectivity resolved: {provenance.reflectivity_resolved}/"
            f"{provenance.total_requested} · "
            f"Echo tops resolved: {provenance.echo_top_resolved}/"
            f"{provenance.total_requested} · "
            f"Both products resolved: {provenance.both_resolved}/"
            f"{provenance.total_requested}"
        )
        excluded = [record for record in provenance.observations if not record.used]
        if not excluded:
            st.caption("All resolved observation pairs used in the verification composite.")
            return
        rows = []
        for record in excluded:
            rows.append({
                "Requested UTC": record.requested_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Reflectivity UTC": (
                    record.reflectivity_time.strftime("%H:%M:%S")
                    if record.reflectivity_time else "Unavailable"
                ),
                "Refl offset (s)": record.reflectivity_offset_seconds,
                "Echo top UTC": (
                    record.echo_top_time.strftime("%H:%M:%S")
                    if record.echo_top_time else "Unavailable"
                ),
                "Top offset (s)": record.echo_top_offset_seconds,
                "Pair separation (s)": record.product_separation_seconds,
                "Grid compatible": record.grid_compatible,
                "Reason": record.exclusion_reason,
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)


def _feature_label(row, *, pipeline):
    if row.kind == "forecast":
        coverage = pipeline._coverage_label(row.feat_type, row.coverage_code)
        feature_type = "Line" if row.feat_type == "LINE" else "Area"
        return f"{row.idx} · {coverage} {feature_type}"
    if row.kind == "candidate_miss":
        return f"M{row.idx} · Candidate Miss"
    return f"F{row.idx} · Review cue"


def _default_review_result(row):
    if row.kind == "candidate_miss":
        return "Missed"
    if row.category == "Overforecasted":
        return "Overforecast"
    if row.category in ("Verified Well", "Verified Close"):
        return row.category
    return "Verified Close"


def _review_result(row):
    value = getattr(row, "review_result", None)
    if value is not None and not (isinstance(value, float) and np.isnan(value)):
        value = str(value)
        if value in REVIEW_RESULTS:
            return value
    return _default_review_result(row)


def _build_reviewed_report(review_table, valid_dt, issuance_hour, lead_time, *, pipeline):
    """Build the human-reviewed FAA draft without changing objective scoring.

    The automated pipeline report remains the default.  The workstation adds an
    optional ``review_result`` column only after a meteorologist applies edits;
    this formatter honors that disposition while keeping the objective category
    and map untouched as provenance.
    """
    doc_report = {
        "Verified Well:": [],
        "Verified Close:": [],
        "Over-forecast:": [],
        "Missed:": [],
    }
    target_section = {
        "Verified Well": "Verified Well:",
        "Verified Close": "Verified Close:",
        "Overforecast": "Over-forecast:",
        "Missed": "Missed:",
    }

    for row in review_table.itertuples(index=False):
        if row.kind == "medium_core_review_flag":
            continue
        if not row.approved_for_report:
            continue

        result = _review_result(row)
        section = target_section[result]

        if row.kind == "candidate_miss":
            if result == "Missed":
                line_text = f"{row.artccs} - Missed (Area M{row.idx})"
            else:
                line_text = f"{row.artccs} - Sparse (Area M{row.idx})"
        else:
            cov_label = pipeline._coverage_label(row.feat_type, row.coverage_code)
            feat_label = "Line" if row.feat_type == "LINE" else "Area"
            line_text = f"{row.artccs} - {cov_label} ({feat_label} {row.idx})"

        doc_report[section].append(line_text)

    report_text = (
        f"National System Review\nNWS TCF Review\n"
        f"{valid_dt.strftime('%A, %B %d, %Y')}\n"
    )
    report_text += (
        f"  {valid_dt.strftime('%b %d, %Y')}   IT: {issuance_hour:02d}Z   "
        f"VT: {valid_dt.strftime('%H')}Z   FCST HR: {lead_time:02d}\n"
    )
    for category, items in doc_report.items():
        report_text += f"{category}\n"
        if not items:
            report_text += "None\n"
        for item in items:
            report_text += f"{item}\n"
        report_text += "\n"
    return report_text


def _render_review_panel(R, *, pipeline):
    st.caption(
        "Set the reviewed result, adjust ARTCC attribution, and choose what enters "
        "the FAA draft. Changes are batched until **Apply Review Changes**, so "
        "checkboxes and dropdowns no longer rerun the page one click at a time."
    )

    table = R["review_table"]
    main_mask = table["kind"].isin(["forecast", "candidate_miss"])
    main_table = table.loc[main_mask].copy()

    if main_table.empty:
        st.info("No forecast or Candidate Miss rows are available for review.")
    else:
        display = main_table[["artccs", "approved_for_report"]].copy()
        display.insert(
            0,
            "Feature",
            [_feature_label(row, pipeline=pipeline)
             for row in main_table.itertuples(index=False)],
        )
        display.insert(
            1,
            "Result",
            [_review_result(row) for row in main_table.itertuples(index=False)],
        )
        display = display.rename(columns={
            "artccs": "ARTCCs",
            "approved_for_report": "FAA",
        })

        editor_key = (
            f"methodology_review_compact_"
            f"{R['valid_dt']:%Y%m%d%H}_"
            f"{R['issuance_hour']:02d}_F{R['lead_time']:02d}"
        )
        form_key = f"{editor_key}_form"
        with st.form(form_key, clear_on_submit=False):
            edited = st.data_editor(
                display,
                hide_index=True,
                use_container_width=True,
                height=min(520, 38 * (len(display) + 1) + 6),
                disabled=["Feature"],
                column_config={
                    "Feature": st.column_config.TextColumn("Feature", width="medium"),
                    "Result": st.column_config.SelectboxColumn(
                        "Result",
                        options=REVIEW_RESULTS,
                        required=True,
                        help=(
                            "Meteorologist-reviewed disposition used in the FAA draft. "
                            "The objective map and score remain unchanged."
                        ),
                        width="medium",
                    ),
                    "ARTCCs": st.column_config.TextColumn(
                        "ARTCCs",
                        help="Editable FAA-facing ARTCC attribution.",
                        width="medium",
                    ),
                    "FAA": st.column_config.CheckboxColumn(
                        "FAA",
                        help="Include this reviewed item in the FAA report.",
                        width="small",
                    ),
                },
                key=editor_key,
            )
            submitted = st.form_submit_button(
                "Apply Review Changes", use_container_width=True, type="primary"
            )

        if submitted:
            updated = table.copy()
            if "review_result" not in updated.columns:
                updated["review_result"] = None
            for position, original_index in enumerate(main_table.index):
                updated.at[original_index, "artccs"] = edited.iloc[position]["ARTCCs"]
                updated.at[original_index, "approved_for_report"] = edited.iloc[position]["FAA"]
                updated.at[original_index, "review_result"] = edited.iloc[position]["Result"]
            R["review_table"] = updated.astype(pipeline.REVIEW_COLUMNS)
            st.success("Review changes applied to the FAA draft.")

    R["report_text"] = _build_reviewed_report(
        R["review_table"], R["valid_dt"], R["issuance_hour"], R["lead_time"],
        pipeline=pipeline,
    )

    review_flags = R["review_table"][
        R["review_table"]["kind"] == "medium_core_review_flag"
    ]
    details_label = "Reviewer details"
    if not review_flags.empty:
        details_label += f" · {len(review_flags)} Medium-core cue(s)"
    with st.expander(details_label):
        st.caption(
            "Full methodology review fields remain available here. Medium-core "
            "rows are factual reviewer cues and have no path into FAA text."
        )
        st.dataframe(R["review_table"], hide_index=True, use_container_width=True)


def _render_report_panel(R):
    escaped = html.escape(R["report_text"])
    st.markdown(
        f'<div style="font-family: Calibri, Arial, sans-serif; font-size: 16px; '
        f'line-height: 1.35; background-color: white; color: black; padding: 14px; '
        f'white-space: pre-wrap; overflow: auto; max-height: 610px; '
        f'border-radius: 6px;">{escaped}</div>',
        unsafe_allow_html=True,
    )
    st.download_button(
        "Pass A",
        R["report_text"],
        file_name="pass_a_report.txt",
        use_container_width=True,
    )


def _render_copy_codes_button(codes_text):
    """One-click clipboard helper with a legacy fallback for locked-down browsers."""
    if not codes_text:
        return
    payload = json.dumps(codes_text)
    fallback = html.escape(codes_text, quote=True)
    components.html(
        f"""
        <div style="display:flex;align-items:center;gap:10px;font-family:system-ui,sans-serif;">
          <button id="copy-codes" onclick="copyCodes()"
            style="border:0;border-radius:8px;padding:8px 14px;font-weight:700;cursor:pointer;">
            Copy Codes
          </button>
          <span id="copy-status" style="font-size:13px;opacity:.75"></span>
          <textarea id="copy-fallback" aria-hidden="true"
            style="position:absolute;left:-10000px;top:-10000px;">{fallback}</textarea>
        </div>
        <script>
          async function copyCodes() {{
            const text = {payload};
            const status = document.getElementById('copy-status');
            try {{
              await navigator.clipboard.writeText(text);
            }} catch (err) {{
              const box = document.getElementById('copy-fallback');
              box.value = text;
              box.focus();
              box.select();
              document.execCommand('copy');
            }}
            status.textContent = 'Copied!';
            window.setTimeout(() => status.textContent = '', 1200);
          }}
        </script>
        """,
        height=46,
    )


def _render_participants():
    st.session_state.setdefault("tcf_participants_raw", "")
    st.session_state.setdefault("tcf_participants_include_orgs", True)
    st.session_state.setdefault("tcf_participants_strict_short", True)

    current = tcf_participants.parse_participants(
        st.session_state["tcf_participants_raw"],
        include_orgs=st.session_state["tcf_participants_include_orgs"],
        strict_short=st.session_state["tcf_participants_strict_short"],
    )
    unknown_text = (
        f" · {len(current.unknown)} unmapped" if current.unknown else ""
    )
    label = f"TCF Participants · {len(current.codes)} recognized{unknown_text}"

    with st.expander(label, expanded=bool(st.session_state["tcf_participants_raw"])):
        raw = st.text_area(
            "Paste TCF chat participants",
            key="tcf_participants_raw",
            height=140,
            placeholder="Paste the participant list from the TCF chat log…",
        )
        option_left, option_right = st.columns(2)
        with option_left:
            include_orgs = st.checkbox(
                "Include organizations",
                key="tcf_participants_include_orgs",
                help="Include AWC, MSC, airlines, NAM forecasters, and similar participants.",
            )
        with option_right:
            strict_short = st.checkbox(
                "Safer short aliases",
                key="tcf_participants_strict_short",
                help="Avoid ambiguous KC, DC, and MIA substring matches.",
            )

        result = tcf_participants.parse_participants(
            raw,
            include_orgs=include_orgs,
            strict_short=strict_short,
        )
        codes_text = ", ".join(result.codes)

        st.markdown("**Collaboration field**")
        st.code(codes_text if codes_text else "—", language=None, wrap_lines=True)
        _render_copy_codes_button(codes_text)
        if codes_text:
            st.caption(f"PowerPoint preview: Collaboration:  {codes_text}")

        cwsu_col, org_col = st.columns(2)
        with cwsu_col:
            st.markdown("**CWSUs**")
            st.code(", ".join(result.cwsus) if result.cwsus else "—", language=None)
        with org_col:
            st.markdown("**Other participants**")
            st.code(", ".join(result.organizations) if result.organizations else "—", language=None)

        if result.unknown:
            st.warning("Unmapped: " + "; ".join(result.unknown))
        st.caption(
            "Copy Codes copies the complete comma-separated participant list for "
            "the PowerPoint Collaboration field. Participant parsing remains a "
            "reviewer utility and does not alter verification scoring."
        )


def render_workstation(
    R,
    *,
    pipeline,
    new_map_fig,
    geom_to_xy,
    gdf_to_xy,
    composite_label,
    duration,
):
    """Render the map-centered TCF verification workstation."""
    st.markdown("---")
    st.caption(
        f"Case: {R['valid_dt']:%b %d, %Y} · "
        f"IT {R['issuance_hour']:02d}Z · VT {R['valid_dt']:%H}Z · "
        f"FH {R['lead_time']:02d}"
    )
    _render_mrms_summary(R.get("mrms_provenance"), duration=duration)

    map_column, inspector_column = st.columns([2.45, 1], gap="large")

    with map_column:
        selector_key = (
            f"tcf_map_view_{R['valid_dt']:%Y%m%d%H}_"
            f"{R['issuance_hour']:02d}_F{R['lead_time']:02d}"
        )
        view = st.segmented_control(
            "Map view",
            options=["Verification", "Reanalysis"],
            default="Verification",
            key=selector_key,
            label_visibility="collapsed",
        )
        if view == "Reanalysis":
            st.caption(
                "Diagnostic temporal-max radar background. Decision 1A pair-first "
                "qualification feeds the displayed truth transformation."
            )
            fig = _reanalysis_figure(
                R, new_map_fig=new_map_fig, gdf_to_xy=gdf_to_xy
            )
        else:
            fig = _scorecard_figure(
                R,
                pipeline=pipeline,
                new_map_fig=new_map_fig,
                geom_to_xy=geom_to_xy,
                composite_label=composite_label,
            )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config=PLOT_CONFIG,
            key=f"{selector_key}_{view or 'Verification'}_plot",
        )
        _render_participants()

    with inspector_column:
        st.subheader("Meteorologist Review")
        review_tab, report_tab = st.tabs(["Review", "FAA Report"])
        with review_tab:
            _render_review_panel(R, pipeline=pipeline)
        with report_tab:
            _render_report_panel(R)
