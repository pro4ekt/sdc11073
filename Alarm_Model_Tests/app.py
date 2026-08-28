from collections import deque

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import streamlit as st

st.set_page_config(
    page_title="Clinical Risk Filter — Decision Axis",
    page_icon="🏥",
    layout="wide",
)

# ── Page header ───────────────────────────────────────────────────────────────
st.title("🏥 Clinical Risk Filter — Dynamic Decision Axis")
st.caption(
    "IEEE 11073 SDC · Log-Odds two-stage alarm filter · "
    "Configure VMD channel activity and observe escalation behaviour in real time."
)
st.divider()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Algorithm Hyperparameters")
    alpha = st.slider(
        "α  — SDC highest prior. weight", 0.0, 1.0, 0.7, 0.05,
        help="SDC Score = α·(max(v_j)/P_max) + (1−α)·(Σv_j/ΣP_j)  — normalised convex combination",
    )
    T = st.slider(
        "T  — Sliding window (s)", 1, 15, 5, 1,
        help="Also used as the memory time horizon for adaptive release: "
             "ρ(t) = (1 − SDC_score(t)) / T  — higher T → slower threshold recovery",
    )

    st.header("📊 SDC Priorities  P_j  (VMD Channels)")
    p_ecg  = st.selectbox("P — ECG",        [0, 1, 2, 3], index=1)
    p_spo2 = st.selectbox("P — SpO₂",       [0, 1, 2, 3], index=2)
    p_vent = st.selectbox("P — Ventilator", [0, 1, 2, 3], index=3)
    p_nibp = st.selectbox("P — NIBP",       [0, 1, 2, 3], index=1)
    P_array = np.array([p_ecg, p_spo2, p_vent, p_nibp])

    st.header("🩺 Patient Context")
    context_log_odds = st.number_input(
        "Context Log-Odds", value=-1.104, step=0.1,
        help="ln(prior_risk / (1−prior_risk)) adjusted for comorbidities. "
             "More negative → higher personalised threshold → filter more conservative.",
    )

    st.divider()
    st.caption(
        "Sensor weights:  w_j = ln(TPR_j / FPR_j)\n\n"
        "TPR = [0.95, 0.90, 0.98, 0.85]  ·  FPR = [0.05, 0.10, 0.02, 0.15]"
    )

# ── Section 1 — VMD Channel Activity Builder ──────────────────────────────────
st.subheader("① VMD Channel Alarm Timeline")

col_time, _ = st.columns([1, 3])
with col_time:
    sim_time = st.slider("Simulation duration (seconds)", 10, 60, 20)

# Sub-second time grid
DT     = 0.1
t_fine = np.arange(0, sim_time + DT, DT)
n_fine = len(t_fine)
t_sec  = np.arange(0, sim_time + 1, dtype=int)

vmd_channels = ['ECG', 'SpO₂', 'Ventilator', 'NIBP']
colors        = ['#e74c3c', '#3498db', '#2ecc71', '#9b59b6']
signals_fine  = np.zeros((n_fine, 4))

sensor_cols = st.columns(4)
for j, (dev, col) in enumerate(zip(vmd_channels, sensor_cols)):
    with col:
        with st.container(border=True):
            st.markdown(f"**{dev}**")
            active = st.checkbox(
                "Active alarm",
                value=(dev == 'Ventilator'),
                key=f"check_{j}",
            )
            if active:
                start1, end1 = st.slider(
                    "Interval 1 (s)",
                    0, sim_time, (1, min(10, sim_time)),
                    key=f"slider1_{j}",
                )
                idx_s1 = int(round(start1 / DT))
                idx_e1 = min(int(round((end1 + 1) / DT)), n_fine)
                signals_fine[idx_s1:idx_e1, j] = 1.0

                has_second = st.checkbox(
                    "Add 2nd interval (reconnection)",
                    value=False,
                    key=f"check_second_{j}",
                )
                if has_second:
                    def_s2 = min(end1 + 3, sim_time)
                    def_e2 = min(def_s2 + 5, sim_time)
                    start2, end2 = st.slider(
                        "Interval 2 (s)",
                        0, sim_time, (def_s2, def_e2),
                        key=f"slider2_{j}",
                    )
                    idx_s2 = int(round(start2 / DT))
                    idx_e2 = min(int(round((end2 + 1) / DT)), n_fine)
                    signals_fine[idx_s2:idx_e2, j] = 1.0

st.divider()

# ── Core simulation on fine grid ──────────────────────────────────────────────
M   = 4
tpr = np.array([0.95, 0.90, 0.98, 0.85])
fpr = np.array([0.05, 0.10, 0.02, 0.15])
w     = np.log(tpr / fpr)
w_avg = float(np.mean(w))

# Θ_init = system steady-state (no alarms → SDC=0 → k_min=M)
prev_theta_init = float(M * w_avg - context_log_odds)

# Pre-compute normalisation constants (protect against all-zero P)
p_max_M = float(np.max(P_array)) if np.max(P_array) > 0 else 1.0
sum_P   = float(np.sum(P_array)) if np.sum(P_array) > 0 else 1.0

buffer_size = max(1, int(round(T / DT)))

s_hist            = np.zeros((n_fine, M))
evidence_hist     = np.zeros(n_fine)
sdc_hist          = np.zeros(n_fine)
k_min_hist        = np.zeros(n_fine, dtype=int)
theta_target_hist = np.zeros(n_fine)
theta_hist        = np.zeros(n_fine)
rho_hist          = np.zeros(n_fine)
escalation_hist   = np.zeros(n_fine, dtype=bool)

buffers    = [deque([0.0] * buffer_size, maxlen=buffer_size) for _ in range(M)]
theta_prev = prev_theta_init

for i in range(n_fine):
    sig = signals_fine[i]

    for j in range(M):
        buffers[j].append(float(sig[j]))
        s_hist[i, j] = float(np.mean(buffers[j]))

    ev = float(sum(w[j] * s_hist[i, j] for j in range(M)))
    evidence_hist[i] = ev

    # 1. Normalised convex-combination SDC Score
    v           = P_array * sig
    max_v_norm  = float(np.max(v)) / p_max_M
    sum_v_norm  = float(np.sum(v)) / sum_P
    sdc         = alpha * max_v_norm + (1.0 - alpha) * sum_v_norm
    sdc_hist[i] = sdc

    # 2. Dynamic k_min (no gamma — derived directly from SDC score)
    k_min         = int(np.floor(M - (M - 2) * sdc))
    k_min_hist[i] = k_min

    # 3. Target threshold
    theta_target        = k_min * w_avg - float(context_log_odds)
    theta_target_hist[i] = theta_target

    # 4. Adaptive rho(t) & asymmetric exponential hysteresis
    rho_t       = (1.0 - sdc) / float(T)
    rho_hist[i] = rho_t

    if theta_target <= theta_prev:
        # Fast attack — drop instantly
        theta_curr = theta_target
    else:
        # Slow exponential release
        theta_curr = theta_target + (theta_prev - theta_target) * np.exp(-rho_t * DT)

    theta_hist[i] = theta_curr
    theta_prev    = theta_curr

    escalation_hist[i] = ev >= theta_curr

# ── Section 2 — KPI metrics ───────────────────────────────────────────────────
st.subheader("② Simulation Summary")

n_esc          = int(escalation_hist.sum())
total_esc_time = n_esc * DT
first_esc_t    = float(t_fine[escalation_hist][0]) if n_esc > 0 else None
max_evidence   = float(evidence_hist.max())
min_theta      = float(theta_hist.min())
alarm_fraction = total_esc_time / sim_time * 100

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total escalation time", f"{total_esc_time:.1f} s",
          help="Cumulative seconds during which Evidence ≥ Θ_current(t)")
m2.metric("First escalation at",
          f"t = {first_esc_t:.1f} s" if first_esc_t is not None else "—")
m3.metric("Peak evidence",            f"{max_evidence:.3f}")
m4.metric("Min threshold Θ_current",  f"{min_theta:.3f}")
m5.metric("Alarm fraction",           f"{alarm_fraction:.1f} %")

st.divider()

# ── Section 3 — Plots ─────────────────────────────────────────────────────────
st.subheader("③ Decision Axis Visualisation")

sec_idx = [int(round(s / DT)) for s in t_sec if int(round(s / DT)) < n_fine]

fig = plt.figure(figsize=(12, 8))
gs  = gridspec.GridSpec(2, 1, hspace=0.42)
ax_heat = fig.add_subplot(gs[0])
ax_dec  = fig.add_subplot(gs[1])

# Panel 1: VMD Channel activity heatmap
for j in range(4):
    row = signals_fine[:, j]
    ax_heat.fill_between(
        t_fine, j - 0.28, j + 0.28,
        where=(row > 0.5), color=colors[j], alpha=0.82, step='mid',
    )
    ax_heat.fill_between(
        t_fine, j - 0.28, j + 0.28,
        where=(row <= 0.5), color='#ecf0f1', alpha=0.55, step='mid',
    )

ax_heat.set_yticks(range(4))
ax_heat.set_yticklabels(
    [f'{d}  (w={w[j]:.2f}, P={int(P_array[j])})' for j, d in enumerate(vmd_channels)],
    fontsize=9,
)
ax_heat.set_xlim(-0.5, sim_time + 0.5)
ax_heat.set_ylim(-0.5, 3.5)
ax_heat.set_xlabel("Time (seconds)", fontsize=9)
ax_heat.set_title(
    "Panel 1 — VMD Channel Alarm Activity  &  Algorithm Weights",
    fontweight='bold', fontsize=10,
)
ax_heat.grid(True, axis='x', linestyle='--', alpha=0.4)
if sim_time <= 30:
    ax_heat.set_xticks(t_sec)

# Panel 2: Decision axis
ax_dec.plot(t_fine, evidence_hist,
            color='#27ae60', linewidth=2.0, zorder=3,
            label='Accumulated Evidence  Σ w_j·s_j(t)')
ax_dec.plot(t_fine, theta_target_hist,
            '--', color='#2980b9', linewidth=2.0, alpha=0.95, zorder=5,
            label='Target Threshold  Θ_target(t)')
ax_dec.plot(t_fine, theta_hist,
            color='#e74c3c', linewidth=2.0, zorder=4,
            label='Θ_current(t)  [adaptive exp. release]')

# Sparse markers at whole seconds
ax_dec.plot(t_fine[sec_idx], evidence_hist[sec_idx],
            '^', color='#27ae60', markersize=6, zorder=6, linewidth=0)
ax_dec.plot(t_fine[sec_idx], theta_hist[sec_idx],
            'o', color='#e74c3c', markersize=5, zorder=6, linewidth=0)

# Escalation fill
ax_dec.fill_between(
    t_fine, theta_hist, evidence_hist,
    where=escalation_hist,
    color='#ffcccc', alpha=0.55, step='mid', zorder=1,
    label='⚠ ESCALATION  (Evidence ≥ Θ_current)',
)

ax_dec.set_title(
    "Panel 2 — Escalation Decision Axis: Accumulated Evidence vs Θ_current(t)",
    fontweight='bold', fontsize=10,
)
ax_dec.set_ylabel("Log-Odds Scale", fontweight='bold')
ax_dec.set_xlabel("Simulation Time (seconds)", fontweight='bold')
ax_dec.legend(loc='upper right', fontsize='small', framealpha=0.92)
ax_dec.grid(True, linestyle='--', alpha=0.45)
ax_dec.set_xlim(-0.5, sim_time + 0.5)
if sim_time <= 30:
    ax_dec.set_xticks(t_sec)
else:
    ax_dec.set_xticks(np.arange(0, sim_time + 1, 5 if sim_time <= 40 else 10))

plt.tight_layout()
st.pyplot(fig)

# ── Section 4 — Step table ────────────────────────────────────────────────────
st.divider()
st.subheader("④ Step-by-Step Computation Table")

show_hires = st.toggle("Show high-resolution data (every 0.1 s)", value=False)

with st.expander("Show / hide table", expanded=False):
    sample_idx = list(range(n_fine)) if show_hires else sec_idx
    table_data = []
    for i in sample_idx:
        table_data.append({
            "t (s)":      round(float(t_fine[i]), 1),
            "ECG":        int(signals_fine[i, 0]),
            "SpO₂":       int(signals_fine[i, 1]),
            "Vent":       int(signals_fine[i, 2]),
            "NIBP":       int(signals_fine[i, 3]),
            "SDC Score":  round(float(sdc_hist[i]), 4),
            "k_min":      int(k_min_hist[i]),
            "ρ(t)":       round(float(rho_hist[i]), 4),
            "Θ_target":   round(float(theta_target_hist[i]), 4),
            "Θ_current":  round(float(theta_hist[i]), 4),
            "Evidence":   round(float(evidence_hist[i]), 4),
            "Escalation": "🚨 YES" if escalation_hist[i] else "🟢 NO",
        })
    st.dataframe(table_data, use_container_width=True)

