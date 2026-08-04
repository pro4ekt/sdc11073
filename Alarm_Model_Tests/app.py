from collections import deque
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

st.set_page_config(page_title="Dynamic Decision Axis", layout="wide")

st.title("🎯 Dynamic Escalation Decision Axis")
st.caption(
    "Конструктор сигналов тревог: настройка активности приборов и фильтр клинических рисков"
)

# ── 1. Кастомная настройка сигналов датчиков ─────────────────────────────────
st.subheader("1. Настройка активности тревог датчиков")

col_time, _ = st.columns([1, 2])
with col_time:
    sim_time = st.slider("Длительность симуляции (секунд)", 10, 60, 20)

t = np.arange(0, sim_time + 1)
n_steps = len(t)
devices = ['ECG', 'SpO2', 'Ventilator', 'NIBP']
signals = np.zeros((n_steps, 4))

cols = st.columns(4)
for j, dev in enumerate(devices):
    with cols[j]:
        st.markdown(f"**{dev} Monitor**")
        active = st.checkbox(
            f"Активировать {dev}",
            value=(dev == 'Ventilator'),
            key=f"check_{dev}",
        )
        if active:
            start, end = st.slider(
                f"Интервал {dev} (сек)",
                0,
                sim_time,
                (1, min(10, sim_time)),
                key=f"slider_{dev}",
            )
            signals[start : end + 1, j] = 1.0

# ── 2. Sidebar: Гиперпараметры и Приоритеты ─────────────────────────────────
st.sidebar.header("⚙️ Гиперпараметры алгоритма")
alpha = st.sidebar.slider("α (Вес пика SDC)", 0.0, 1.0, 0.7, 0.05)
beta = st.sidebar.slider("β (Вес фона SDC)", 0.0, 1.0, 0.3, 0.05)
gamma = st.sidebar.slider("γ (Чувствительность k_min)", 0.1, 2.0, 1.0, 0.1)
rho = st.sidebar.slider(
    "ρ (Скорость восстановления Θ, 1/с)", 0.01, 1.0, 0.2, 0.01
)
T = st.sidebar.slider("T (Размер окна, сек)", 1, 15, 5, 1)

st.sidebar.header("📊 Приоритеты SDC (P_j)")
p_ecg = st.sidebar.selectbox("P (ECG)", [0, 1, 2, 3], index=1)
p_spo2 = st.sidebar.selectbox("P (SpO2)", [0, 1, 2, 3], index=2)
p_vent = st.sidebar.selectbox("P (Ventilator)", [0, 1, 2, 3], index=3)
p_nibp = st.sidebar.selectbox("P (NIBP)", [0, 1, 2, 3], index=1)
P_array = np.array([p_ecg, p_spo2, p_vent, p_nibp])

st.sidebar.header("🩺 Контекст пациента")
context_log_odds = st.sidebar.number_input(
    "Context_Log_Odds", value=-1.104, step=0.1
)
prev_theta_init = st.sidebar.number_input(
    "Θ_init (Начальный порог)", value=11.868, step=0.5
)

# ── 3. Расчет математической модели ─────────────────────────────────────────
M = 4
tpr = np.array([0.95, 0.90, 0.98, 0.85])
fpr = np.array([0.05, 0.10, 0.02, 0.15])
w = np.log(tpr / fpr)
w_avg = float(np.mean(w))

s_hist = np.zeros((n_steps, M))
evidence_hist = np.zeros(n_steps)
sdc_hist = np.zeros(n_steps)
k_min_hist = np.zeros(n_steps)
theta_target_hist = np.zeros(n_steps)
theta_hist = np.zeros(n_steps)
escalation_hist = np.zeros(n_steps, dtype=bool)

buffers = [deque([0.0] * T, maxlen=T) for _ in range(M)]
theta_prev = prev_theta_init

for i in range(n_steps):
    sig = signals[i]
    for j in range(M):
        buffers[j].append(float(sig[j]))
        s_hist[i, j] = float(np.mean(buffers[j]))

    ev = float(sum(w[j] * s_hist[i, j] for j in range(M)))
    evidence_hist[i] = ev

    v = P_array * sig
    sdc = alpha * float(np.max(v)) + beta * float(np.mean(v))
    sdc_hist[i] = sdc

    k_min = max(2, int(np.floor(M - gamma * sdc)))
    k_min_hist[i] = k_min

    theta_target = k_min * w_avg - context_log_odds
    theta_target_hist[i] = theta_target

    theta_curr = min(theta_prev + rho * 1.0, theta_target)
    theta_hist[i] = theta_curr
    theta_prev = theta_curr

    escalation_hist[i] = ev >= theta_curr

# ── 4. График Decision Axis ──────────────────────────────────────────────────
st.subheader("2. Ось принятия решений")
fig, ax = plt.subplots(figsize=(11, 3.8))

ax.plot(
    t,
    evidence_hist,
    'g-^',
    linewidth=2.5,
    markersize=6,
    zorder=3,
    label='Evidence sum(w_j * s_j)',
)
ax.plot(
    t,
    theta_target_hist,
    'r--',
    linewidth=2.0,
    alpha=0.90,
    zorder=5,
    label='Target threshold Theta_target',
)
ax.plot(
    t,
    theta_hist,
    'r-o',
    linewidth=2.5,
    markersize=6,
    zorder=4,
    label='Current threshold Theta(t) (Hysteresis)',
)

label_added = False
for i in range(n_steps):
    if escalation_hist[i]:
        lbl = 'ESCALATION (ALARM ACTIVE)' if not label_added else ''
        ax.axvspan(
            t[i] - 0.5, t[i] + 0.5, color='#ffcccc', alpha=0.35, label=lbl, zorder=1
        )
        label_added = True

ax.set_title(
    "Decision Axis: Accumulated Evidence vs Personalised Threshold",
    fontsize=12,
    fontweight="bold",
)
ax.set_ylabel("Log-Odds Scale", fontweight="bold")
ax.set_xlabel("Simulation Time (seconds)", fontweight="bold")
ax.legend(loc="upper left", fontsize="small", framealpha=0.9)
ax.grid(True, linestyle="--", alpha=0.5)

if sim_time > 20:
    ax.set_xticks(np.arange(0, sim_time + 1, 5 if sim_time <= 40 else 10))
else:
    ax.set_xticks(t)

plt.tight_layout()
st.pyplot(fig)

# ── 5. Таблица вычислений ───────────────────────────────────────────────────
st.subheader("📋 Данные шагов")
table_data = []
for i in range(n_steps):
    table_data.append({
        "t (s)": int(t[i]),
        "ECG": int(signals[i, 0]),
        "SpO2": int(signals[i, 1]),
        "Vent": int(signals[i, 2]),
        "NIBP": int(signals[i, 3]),
        "SDC Score": round(sdc_hist[i], 3),
        "k_min": int(k_min_hist[i]),
        "Θ_target": round(theta_target_hist[i], 3),
        "Θ(t)": round(theta_hist[i], 3),
        "Evidence": round(evidence_hist[i], 3),
        "Escalation": "🚨 YES" if escalation_hist[i] else "🟢 NO",
    })
st.dataframe(table_data, use_container_width=True)