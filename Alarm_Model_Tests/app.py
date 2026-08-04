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

# ── Page header ──────────────────────────────────────────────────────────────
st.title("🏥 Clinical Risk Filter — Dynamic Decision Axis")
st.caption(
    "IEEE 11073 SDC · Log-Odds two-stage alarm filter · "
    "Configure sensor activity and observe escalation behaviour in real time."
)
st.divider()

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Algorithm Hyperparameters")
    alpha = st.slider("α  — SDC peak weight",        0.0, 1.0, 0.7, 0.05)
    beta  = st.slider("β  — SDC background weight",  0.0, 1.0, 0.3, 0.05)
    gamma = st.slider("γ  — k_min sensitivity",      0.1, 2.0, 1.0, 0.1)
    rho   = st.slider("ρ  — Threshold recovery (1/s)", 0.01, 1.0, 0.2, 0.01)
    T     = st.slider("T  — Sliding window (s)",     1,   15,  5,   1)

    st.header("📊 SDC Priorities  P_j")
    p_ecg  = st.selectbox("P — ECG",        [0, 1, 2, 3], index=1)
    p_spo2 = st.selectbox("P — SpO₂",       [0, 1, 2, 3], index=2)
    p_vent = st.selectbox("P — Ventilator", [0, 1, 2, 3], index=3)
    p_nibp = st.selectbox("P — NIBP",       [0, 1, 2, 3], index=1)
    P_array = np.array([p_ecg, p_spo2, p_vent, p_nibp])

    st.header("🩺 Patient Context")
    context_log_odds = st.number_input(
        "Context Log-Odds", value=-1.104, step=0.1,
        help="ln(prior_risk / (1 - prior_risk)) adjusted for comorbidities"
    )

    st.divider()
    st.caption(
        "Sensor weights are computed as  w_j = ln(TPR_j / FPR_j).\n\n"
        "TPR = [0.95, 0.90, 0.98, 0.85]  ·  FPR = [0.05, 0.10, 0.02, 0.15]"
    )

# ── Section 1 — Sensor Activity Builder ─────────────────────────────────────
st.subheader("① Sensor Alarm Timeline")

col_time, _ = st.columns([1, 3])
with col_time:
    sim_time = st.slider("Simulation duration (seconds)", 10, 60, 20)

t       = np.arange(0, sim_time + 1)
n_steps = len(t)
devices = ['ECG', 'SpO₂', 'Ventilator', 'NIBP']
colors  = ['#e74c3c', '#3498db', '#2ecc71', '#9b59b6']
signals = np.zeros((n_steps, 4))

sensor_cols = st.columns(4)
for j, (dev, col) in enumerate(zip(devices, sensor_cols)):
    with col:
        with st.container(border=True):
            st.markdown(f"**{dev}**")
            active = st.checkbox(
                "Active alarm",
                value=(dev == 'Ventilator'),
                key=f"check_{j}",
            )
            if active:
                start, end = st.slider(
                    "Alarm interval (s)",
                    0, sim_time, (1, min(10, sim_time)),
                    key=f"slider_{j}",
                )
                # clamp to valid index range
                end_idx = min(end + 1, n_steps)
                signals[start:end_idx, j] = 1.0

st.divider()

# ── Core simulation ──────────────────────────────────────────────────────────
M   = 4
tpr = np.array([0.95, 0.90, 0.98, 0.85])
fpr = np.array([0.05, 0.10, 0.02, 0.15])
w     = np.log(tpr / fpr)
w_avg = round(float(np.mean(w)), 3)
print(w_avg)

# Θ_init = steady-state threshold when no alarms are active (SDC=0 → k_min=M=4)
# This is a system-derived constant: Θ_init = M · w_avg − context_log_odds
prev_theta_init = M * w_avg - float(context_log_odds)

s_hist            = np.zeros((n_steps, M))
evidence_hist     = np.zeros(n_steps)
sdc_hist          = np.zeros(n_steps)
k_min_hist        = np.zeros(n_steps)
theta_target_hist = np.zeros(n_steps)
theta_hist        = np.zeros(n_steps)
escalation_hist   = np.zeros(n_steps, dtype=bool)

buffers    = [deque([0.0] * T, maxlen=T) for _ in range(M)]
theta_prev = float(prev_theta_init)

for i in range(n_steps):
    sig = signals[i]
    for j in range(M):
        buffers[j].append(float(sig[j]))
        s_hist[i, j] = float(np.mean(buffers[j]))

    ev  = float(sum(w[j] * s_hist[i, j] for j in range(M)))
    evidence_hist[i] = ev

    v   = P_array * sig
    sdc = alpha * float(np.max(v)) + beta * float(np.mean(v))
    sdc_hist[i] = sdc

    k_min         = max(2, int(np.floor(M - gamma * sdc)))
    k_min_hist[i] = k_min

    theta_target        = k_min * w_avg - context_log_odds
    theta_target_hist[i] = theta_target

    theta_curr   = min(theta_prev + rho * 1.0, theta_target)
    theta_hist[i] = theta_curr
    theta_prev   = theta_curr

    escalation_hist[i] = ev >= theta_curr

# ── Section 2 — KPI metrics ──────────────────────────────────────────────────
st.subheader("② Simulation Summary")

n_esc          = int(escalation_hist.sum())
first_esc      = int(t[escalation_hist][0]) if n_esc > 0 else None
max_evidence   = float(evidence_hist.max())
min_theta      = float(theta_hist.min())
alarm_fraction = n_esc / n_steps * 100

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Escalation events",    f"{n_esc} s",
          help="Seconds during which Evidence ≥ Θ(t)")
m2.metric("First escalation at",  f"t = {first_esc} s" if first_esc is not None else "—")
m3.metric("Peak evidence",        f"{max_evidence:.3f}")
m4.metric("Min threshold Θ(t)",   f"{min_theta:.3f}")
m5.metric("Alarm fraction",       f"{alarm_fraction:.1f} %")

st.divider()

# ── Section 3 — Plots ────────────────────────────────────────────────────────
st.subheader("③ Decision Axis Visualisation")

fig = plt.figure(figsize=(12, 8))
gs  = gridspec.GridSpec(2, 1, hspace=0.42)
ax_heat = fig.add_subplot(gs[0])
ax_dec  = fig.add_subplot(gs[1])

# ── Heatmap: sensor active states ───────────────────────────────────────────
heat_colors = ['#e74c3c', '#3498db', '#2ecc71', '#9b59b6']
for j in range(4):
    row = signals[:, j]
    # draw filled blocks where alarm is active
    for i in range(n_steps):
        if row[i] > 0:
            ax_heat.barh(
                j, 1, left=t[i] - 0.5,
                height=0.6, color=heat_colors[j], alpha=0.80,
            )
        else:
            ax_heat.barh(
                j, 1, left=t[i] - 0.5,
                height=0.6, color='#ecf0f1', alpha=0.60,
            )

ax_heat.set_yticks(range(4))
ax_heat.set_yticklabels(
    [f'{d}  (w={w[j]:.2f}, P={int(P_array[j])})' for j, d in enumerate(devices)],
    fontsize=9,
)
ax_heat.set_xlim(-0.5, sim_time + 0.5)
ax_heat.set_xlabel("Time (seconds)", fontsize=9)
ax_heat.set_title("Panel 1 — Sensor Alarm Activity  &  Algorithm Weights",
                  fontweight='bold', fontsize=10)
ax_heat.grid(True, axis='x', linestyle='--', alpha=0.4)
if sim_time <= 20:
    ax_heat.set_xticks(t)

# ── Decision Axis ────────────────────────────────────────────────────────────
ax_dec.plot(
    t, evidence_hist,
    'g-^', linewidth=2.5, markersize=6, zorder=3,
    label='Accumulated Evidence  Σ w_j·s_j(t)',
)
ax_dec.plot(
    t, theta_target_hist,
    '--', color='#2980b9', linewidth=2.5, alpha=0.95, zorder=5,
    label='Target Threshold  Θ_target(t)',
)
ax_dec.plot(
    t, theta_hist,
    'r-o', linewidth=2.5, markersize=5, zorder=4,
    label='Hysteresis Threshold  Θ(t)',
)

label_added = False
for i in range(n_steps):
    if escalation_hist[i]:
        lbl = '⚠ ESCALATION  (ALARM ACTIVE)' if not label_added else ''
        ax_dec.axvspan(
            t[i] - 0.5, t[i] + 0.5,
            color='#ffcccc', alpha=0.45, label=lbl, zorder=1,
        )
        label_added = True

ax_dec.set_title(
    "Panel 2 — Escalation Decision Axis: Evidence vs Personalised Threshold",
    fontweight='bold', fontsize=10,
)
ax_dec.set_ylabel("Log-Odds Scale", fontweight='bold')
ax_dec.set_xlabel("Simulation Time (seconds)", fontweight='bold')
ax_dec.legend(loc='upper right', fontsize='small', framealpha=0.92)
ax_dec.grid(True, linestyle='--', alpha=0.45)
ax_dec.set_xlim(-0.5, sim_time + 0.5)
if sim_time <= 20:
    ax_dec.set_xticks(t)
else:
    ax_dec.set_xticks(np.arange(0, sim_time + 1, 5 if sim_time <= 40 else 10))

plt.tight_layout()
st.pyplot(fig)

# ── Section 4 — Step table ───────────────────────────────────────────────────
st.divider()
st.subheader("④ Step-by-Step Computation Table")

with st.expander("Show / hide table", expanded=False):
    table_data = []
    for i in range(n_steps):
        table_data.append({
            "t (s)":      int(t[i]),
            "ECG":        int(signals[i, 0]),
            "SpO₂":       int(signals[i, 1]),
            "Vent":       int(signals[i, 2]),
            "NIBP":       int(signals[i, 3]),
            "SDC Score":  round(sdc_hist[i], 3),
            "k_min":      int(k_min_hist[i]),
            "Θ_target":   round(theta_target_hist[i], 3),
            "Θ(t)":       round(theta_hist[i], 3),
            "Evidence":   round(evidence_hist[i], 3),
            "Escalation": "🚨 YES" if escalation_hist[i] else "🟢 NO",
        })
    st.dataframe(table_data, use_container_width=True)

