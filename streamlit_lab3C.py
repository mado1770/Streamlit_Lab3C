
# Run: python -m streamlit run streamlit_lab3C.py



import streamlit as st
import pandas as pd
import psycopg2 as pg

st.title("Smart Manufacturing Demo App")
st.markdown("Author: Diego Dominguez (dmdoming@ncsu.edu)")
st.header("Conveyor Belt Sensor Data")

asset = st.text_input("Asset ID", "conveyor01")

if st.button("Query Data"):
    # Read the latest 500 observations for the selected asset.
    qCmd = '''SELECT event_id, event_time, Start_Stop_Command, motor_status, Sensor_Count
              FROM public."sensor_data"
              WHERE asset_id = %s
              ORDER BY event_time DESC, event_id DESC
              LIMIT 500'''

    try:
        DB_URI = "postgresql://db_220plc_user:E3yNpasLSaZGGIlrOPr57LEvs2VEIK3u@dpg-daqmlprncjis7399iong-a.oregon-postgres.render.com/db_220plc"
        db_client = pg.connect(DB_URI, sslmode="require")
        # db_client = pg.connect(st.secrets["DB_URI"], sslmode="require")
        try:
            with db_client.cursor() as cursor:
                cursor.execute(qCmd, (asset,))
                df = pd.DataFrame(cursor.fetchall(),
                                  columns=[col[0] for col in cursor.description])
        finally:
            db_client.close()
    except Exception:
        st.error("Could not query the database. Check connection settings and access.")
        st.stop()

    # Keep the queried snapshot when OEE inputs trigger a Streamlit rerun.
    st.session_state["data"] = df
    st.session_state["data_asset"] = asset

if "data" not in st.session_state:
    st.info("Click Query Data to load observations before entering OEE inputs.")
    st.stop()
if asset != st.session_state["data_asset"]:
    st.info("Asset changed. Click Query Data to load its observations.")
    st.stop()
df = st.session_state["data"].copy()

if df.empty:
    st.info("No data found for this asset.")
else:
    df = df.sort_values(["event_time", "event_id"])
    df["event_time"] = pd.to_datetime(df["event_time"])
    df["seconds"] = (df["event_time"] - df["event_time"].iloc[0]).dt.total_seconds()

    st.dataframe(df, hide_index=True)
    st.subheader("Counter value over elapsed seconds")
    st.line_chart(df, x="seconds", y="sensor_count")
    st.caption("The counter is cumulative. Observation rows are not completed units.")

    st.subheader("KPIs from the selected database observations")
    st.caption(f"Observed window: {df.event_time.iloc[0]} to {df.event_time.iloc[-1]} • {len(df)} samples")
    st.caption("Assumption: one counter increment represents one completed unit.")

    # Output and throughput: first counter is the baseline, not window output.
    elapsed_s = df["seconds"].iloc[-1]
    counter_change = df["sensor_count"].diff()
    valid_counter = (df["sensor_count"].notna().all()
                     and (df["sensor_count"] >= 0).all()
                     and not (counter_change < 0).any())
    if len(df) >= 2 and elapsed_s > 0 and valid_counter:
        output = int(df["sensor_count"].iloc[-1] - df["sensor_count"].iloc[0])
        throughput = output * 3600 / elapsed_s
        left, right = st.columns(2)
        left.metric("Window output", f"{output} units")
        right.metric("Throughput", f"{throughput:.1f} units/hour")
        st.caption(f"{output} / {elapsed_s:.3f} seconds × 3600 = {throughput:.1f} units/hour")
    else:
        st.info("Output/rate unavailable: need two samples, elapsed time and a counter without missing values or resets.")

    # Hold each machine state until the next sample; final row has no duration.
    dt = (df["event_time"].shift(-1) - df["event_time"]).dt.total_seconds()
    valid_state = (len(df) >= 2 and elapsed_s > 0
                   and df["motor_status"].iloc[:-1].notna().all()
                   and dt.iloc[:-1].between(0, 3, inclusive="right").all())
    if valid_state:
        run_s = dt[df["motor_status"].eq(True)].sum()
        stopped_s = elapsed_s - run_s
        running_fraction = run_s / elapsed_s
        left, middle, right = st.columns(3)
        left.metric("Observed running time", f"{run_s:.1f} seconds")
        middle.metric("Observed stopped time", f"{stopped_s:.1f} seconds")
        right.metric("Observed running fraction", f"{running_fraction:.1%}")
    else:
        st.info("Running time unavailable: missing states, duplicate times or a sampling gap above 3 seconds.")
    st.caption("Running fraction is a sampled estimate, not OEE availability. Rate uses first-to-last sample coverage.")


    # Completion spacing is observed at polling times, not active cycle time.
    if valid_counter and counter_change.iloc[1:].isin([0, 1]).all():
        completion_times = df.loc[counter_change.eq(1), "event_time"]
        gaps_s = completion_times.diff().dt.total_seconds().dropna()
        if not gaps_s.empty:
            st.metric("Mean observed completion spacing", f"{gaps_s.mean():.2f} seconds")
            st.caption("Spacing includes stops and polling delay; it is not measured active processing cycle time.")

    st.subheader("OEE: enter real production inputs")
    st.caption("Use the displayed asset and first-to-last observation window. Running time comes from motor_status.")
    # Window-specific keys keep inputs for different snapshots separate.
    window_key = f"{asset}_{df.event_id.iloc[0]}_{df.event_id.iloc[-1]}"
    left, right = st.columns(2)
    planned_min = left.number_input("Planned production time (minutes)",
        min_value=0.0, value=0.0, step=0.1, key="planned_" + window_key)
    ideal_s = right.number_input("Product-specific ideal cycle (seconds/unit)",
        min_value=0.0, value=0.0, step=0.1, key="ideal_" + window_key)
    left, right = st.columns(2)
    total = left.number_input("Total completed units (first-pass inspection)",
        min_value=0, value=0, step=1, key="total_" + window_key)
    good = right.number_input("First-pass good units", min_value=0, value=0,
        step=1, key="good_" + window_key)
    matched = st.checkbox("These inputs match this asset/product/window; measured running time belongs to its planned production time.",
        key="matched_" + window_key)

    planned_s = planned_min * 60
    if not matched:
        st.info("Enter the actual production inputs and confirm the matching window to calculate OEE.")
    elif not valid_state or run_s <= 0:
        st.warning("OEE unavailable: valid positive measured running time is required.")
    elif planned_s <= 0 or ideal_s <= 0 or total <= 0 or good > total:
        st.warning("Planned time, ideal cycle and total must be positive; good cannot exceed total.")
    elif planned_s > elapsed_s + 0.001 or run_s > planned_s + 0.001:
        st.warning("For this window, planned time must fit within observed elapsed time and cannot be shorter than measured running time.")
    else:
        availability = run_s / planned_s
        performance = ideal_s * total / run_s
        quality = good / total
        oee = availability * performance * quality
        a, p, q, o = st.columns(4)
        a.metric("Availability", f"{availability:.1%}")
        p.metric("Performance", f"{performance:.1%}")
        q.metric("Quality", f"{quality:.1%}")
        o.metric("OEE", f"{oee:.1%}")
        if performance > 1:
            st.warning("Performance exceeds 100%: check ideal cycle, counts, units and matching window. Values are not capped.")
        if valid_counter and len(df) >= 2 and elapsed_s > 0 and total != output:
            st.warning("Entered total differs from counter-derived output. Reconcile count definitions and window boundaries.")
        st.caption(f"Measured run time: {run_s:.3f} s; entered planned time: {planned_s:.3f} s.")
    st.caption("A = run / planned; P = ideal cycle × total / run; Q = good / total; OEE = A × P × Q.")
    st.caption("Item readiness/presence signals do not measure inspection quality. No synthetic OEE inputs are supplied.")
