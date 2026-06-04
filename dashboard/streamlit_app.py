from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from dashboard.api_client import DashboardApiError, StoreIntelligenceClient


DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_STORE_ID = "store-1"
DEFAULT_REFRESH_SECONDS = 30


def main() -> None:
    st.set_page_config(
        page_title="Store Intelligence Dashboard",
        page_icon="SI",
        layout="wide",
    )

    api_base_url = os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL)
    default_store_id = os.getenv("STORE_ID", DEFAULT_STORE_ID)
    refresh_seconds = _env_int("DASHBOARD_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS)

    with st.sidebar:
        st.title("Store Intelligence")
        store_id = st.text_input("Store ID", value=default_store_id)
        api_base_url = st.text_input("API Base URL", value=api_base_url)
        refresh_seconds = st.number_input(
            "Refresh seconds",
            min_value=5,
            max_value=300,
            value=refresh_seconds,
            step=5,
        )
        auto_refresh = st.toggle("Auto refresh", value=True)

    if auto_refresh:
        _install_auto_refresh(int(refresh_seconds))

    st.title("Store Intelligence Dashboard")
    st.caption(f"Connected to `{api_base_url}`")

    client = StoreIntelligenceClient(base_url=api_base_url)

    try:
        health = client.health()
        metrics = client.metrics(store_id)
        heatmap = client.heatmap(store_id)
        anomalies = client.anomalies(store_id)
    except DashboardApiError as exc:
        st.error(str(exc))
        st.stop()

    _render_health(health)
    _render_kpis(metrics=metrics, anomalies=anomalies)
    _render_main_sections(heatmap=heatmap, anomalies=anomalies)


def _render_health(health: dict[str, Any]) -> None:
    status = str(health.get("status", "UNKNOWN"))
    last_event = health.get("last_event_timestamp") or "No events yet"
    store_status = health.get("store_status", [])

    status_label = "Healthy" if status == "OK" else "Needs Attention"
    status_method = st.success if status == "OK" else st.warning
    status_method(f"{status_label} | Last event: {last_event}")

    if store_status:
        with st.expander("Store Feed Status", expanded=False):
            st.dataframe(
                pd.DataFrame(store_status),
                hide_index=True,
                use_container_width=True,
            )


def _render_kpis(metrics: dict[str, Any], anomalies: dict[str, Any]) -> None:
    active_anomalies = anomalies.get("anomalies", [])
    conversion_rate = float(metrics.get("conversion_rate", 0.0))

    col1, col2, col3 = st.columns(3)
    col1.metric("Live Visitors", int(metrics.get("unique_visitors", 0)))
    col2.metric("Conversion Rate", f"{conversion_rate:.1%}")
    col3.metric("Active Anomalies", len(active_anomalies))


def _render_main_sections(heatmap: dict[str, Any], anomalies: dict[str, Any]) -> None:
    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Top Zones")
        zone_rows = heatmap.get("zones", [])
        if zone_rows:
            zone_df = pd.DataFrame(zone_rows).sort_values(
                by=["normalized_score", "visit_count"],
                ascending=False,
            )
            st.bar_chart(
                zone_df.set_index("zone")["normalized_score"],
                use_container_width=True,
            )
            st.dataframe(
                zone_df[
                    [
                        "zone",
                        "visit_count",
                        "avg_dwell",
                        "normalized_score",
                        "confidence_flag",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("No zone activity available yet.")

    with right:
        st.subheader("Active Anomalies")
        anomaly_rows = anomalies.get("anomalies", [])
        if not anomaly_rows:
            st.success("No active anomalies detected.")
            return

        for anomaly in anomaly_rows:
            severity = anomaly.get("severity", "LOW")
            message = (
                f"**{anomaly.get('anomaly_type', 'UNKNOWN')}** | {severity}\n\n"
                f"{anomaly.get('description', '')}\n\n"
                f"Suggested action: {anomaly.get('suggested_action', '')}"
            )
            if severity == "HIGH":
                st.error(message)
            elif severity == "MEDIUM":
                st.warning(message)
            else:
                st.info(message)


def _install_auto_refresh(refresh_seconds: int) -> None:
    milliseconds = refresh_seconds * 1000
    components.html(
        f"""
        <script>
            setTimeout(function() {{
                window.parent.location.reload();
            }}, {milliseconds});
        </script>
        """,
        height=0,
    )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    main()
