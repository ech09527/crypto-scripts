(() => {
  const fmt = (n, d = 2) => Number(n).toLocaleString("en-US", {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });

  let chart;

  function selectedLabel() {
    const el = document.getElementById("label-select");
    return el && el.value ? el.value : null;
  }

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function renderSummary(rows) {
    if (!rows || !rows.length) return;
    const label = selectedLabel();
    const s = rows.find((r) => r.label === label) || rows[0];
    const set = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };
    set("v-leverage", `${fmt(s.leverage, 4)}x`);
    set("v-equity", fmt(s.equity, 2));
    set("v-notional", fmt(s.gross_notional, 2));
    set("v-upnl", fmt(s.upnl, 2));
  }

  function renderTable(rows) {
    const body = document.getElementById("history-body");
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="11" class="empty">暂无历史</td></tr>';
      return;
    }
    const desc = [...rows].reverse();
    body.innerHTML = desc
      .map(
        (r) => `<tr>
        <td>${r.ts}</td>
        <td>${r.label}</td>
        <td>${fmt(r.leverage, 4)}</td>
        <td>${fmt(r.equity)}</td>
        <td>${fmt(r.gross_notional)}</td>
        <td>${fmt(r.long_notional)}</td>
        <td>${fmt(r.short_notional)}</td>
        <td>${fmt(r.upnl)}</td>
        <td>${r.position_count}</td>
        <td>${fmt(r.uni_mmr, 4)}</td>
        <td>${fmt(r.available)}</td>
      </tr>`
      )
      .join("");
  }

  function renderChart(rows) {
    const canvas = document.getElementById("lev-chart");
    if (!canvas || typeof Chart === "undefined") return;
    const labels = rows.map((r) => r.ts.replace("T", " ").slice(5, 16));
    const lev = rows.map((r) => r.leverage);
    const equity = rows.map((r) => r.equity);

    if (chart) chart.destroy();
    chart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "账户杠杆",
            data: lev,
            borderColor: "#d4a24c",
            backgroundColor: "rgba(212,162,76,0.12)",
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            yAxisID: "y",
          },
          {
            label: "总权益",
            data: equity,
            borderColor: "#3ecf8e",
            backgroundColor: "transparent",
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 1.5,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#8aa094", boxWidth: 12 },
          },
        },
        scales: {
          x: {
            ticks: { color: "#8aa094", maxTicksLimit: 8 },
            grid: { color: "rgba(180,210,190,0.06)" },
          },
          y: {
            position: "left",
            ticks: { color: "#d4a24c" },
            grid: { color: "rgba(180,210,190,0.06)" },
            title: { display: true, text: "杠杆 x", color: "#8aa094" },
          },
          y1: {
            position: "right",
            ticks: { color: "#3ecf8e" },
            grid: { drawOnChartArea: false },
            title: { display: true, text: "权益", color: "#8aa094" },
          },
        },
      },
    });
  }

  async function refreshHealth() {
    try {
      const h = await fetchJSON("/api/health");
      const el = document.getElementById("last-run");
      if (el) el.textContent = h.last_run || "—";
      const err = document.getElementById("last-error");
      if (err) {
        if (h.last_error) {
          err.hidden = false;
          err.textContent = h.last_error;
        } else {
          err.hidden = true;
          err.textContent = "";
        }
      }
    } catch (_) {}
  }

  async function reloadData() {
    const label = selectedLabel();
    const q = label ? `?label=${encodeURIComponent(label)}&limit=400` : "?limit=400";
    const [latest, history] = await Promise.all([
      fetchJSON("/api/latest"),
      fetchJSON(`/api/history${q}`),
    ]);
    renderSummary(latest);
    renderTable(history);
    renderChart(history);
    await refreshHealth();
  }

  async function collectNow() {
    const btn = document.getElementById("btn-refresh");
    if (btn) btn.disabled = true;
    try {
      await fetchJSON("/api/collect", { method: "POST" });
      await reloadData();
    } catch (e) {
      alert(`采集失败: ${e.message || e}`);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  document.getElementById("btn-refresh")?.addEventListener("click", collectNow);
  document.getElementById("btn-reload")?.addEventListener("click", () => reloadData());
  document.getElementById("label-select")?.addEventListener("change", () => reloadData());

  reloadData();
  setInterval(reloadData, 60_000);
})();
