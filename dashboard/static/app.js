/* 短线看板前端：原生 JS + ECharts */
(() => {
  "use strict";

  // ------------------------------------------------------------ 基础工具
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* 隐私模式等 */ } },
  };

  async function api(url, opts) {
    const r = await fetch(url, opts);
    let j;
    try { j = await r.json(); } catch { throw new Error(`HTTP ${r.status}`); }
    if (!r.ok) throw new Error(j && j.error ? j.error : `HTTP ${r.status}`);
    return j;
  }

  const isNum = (v) => typeof v === "number" && isFinite(v);
  function money(v, d = 2) {
    if (!isNum(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(d) + "亿";
    if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e6 ? 0 : 1) + "万";
    return v.toFixed(0);
  }
  const pct = (v, d = 2, sign = true) => (isNum(v) ? (sign && v > 0 ? "+" : "") + v.toFixed(d) + "%" : "—");
  const pct01 = (v, d = 1, sign = false) => (isNum(v) ? pct(v * 100, d, sign) : "—");
  const cls = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtDate = (s) => (s && s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : s);
  const hhmm = (s) => (s && s.length >= 4 ? `${s.slice(0, 2)}:${s.slice(2, 4)}` : "—");

  // ------------------------------------------------------------ 主题与图表
  const charts = {};
  function theme() {
    return {
      up: cssv("--up"), down: cssv("--down"), accent: cssv("--accent"), text: cssv("--text"),
      text2: cssv("--text-2"), text3: cssv("--text-3"), line: cssv("--line"), panel: cssv("--panel"),
      warn: cssv("--warn"),
      palette: ["#3563e9", "#f59e0b", "#8b5cf6", "#14b8a6", "#ec4899", "#64748b", "#ef4444", "#22c55e"],
    };
  }
  function chart(id) {
    const el = document.getElementById(id);
    if (!charts[id] || charts[id].getDom() !== el) {
      charts[id] && charts[id].dispose();
      charts[id] = echarts.init(el, null, { renderer: "canvas" });
    }
    return charts[id];
  }
  function base(extra = {}) {
    const t = theme();
    return {
      color: t.palette,
      backgroundColor: "transparent",
      textStyle: { color: t.text2, fontFamily: "PingFang SC, Microsoft YaHei, sans-serif" },
      grid: { left: 52, right: 52, top: 36, bottom: 30 },
      tooltip: {
        trigger: "axis", backgroundColor: t.panel, borderColor: t.line, textStyle: { color: t.text, fontSize: 12 },
        axisPointer: { type: "cross", lineStyle: { color: t.text3 }, crossStyle: { color: t.text3 }, label: { backgroundColor: t.text2 } },
      },
      legend: { top: 0, textStyle: { color: t.text2, fontSize: 12 }, itemWidth: 14, itemHeight: 8, icon: "roundRect" },
      ...extra,
    };
  }
  function axis(type = "value", extra = {}) {
    const t = theme();
    const a = {
      type, axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: 11 }, splitLine: { lineStyle: { color: t.line, type: "dashed" } },
    };
    if (type === "category") a.splitLine = { show: false };
    return { ...a, ...extra };
  }
  function emptyChart(id, msg) {
    const t = theme();
    chart(id).setOption({ title: { text: msg, left: "center", top: "middle", textStyle: { color: t.text3, fontSize: 13, fontWeight: 400 } },
      xAxis: { show: false }, yAxis: { show: false }, series: [] }, true);
  }
  function ma(vals, n) {
    const out = []; let s = 0;
    for (let i = 0; i < vals.length; i++) {
      s += vals[i];
      if (i >= n) s -= vals[i - n];
      out.push(i >= n - 1 ? +(s / n).toFixed(3) : null);
    }
    return out;
  }

  /** K线：rows = [date, open, close, low, high, volume, ...] */
  function kline(id, rows, { mas = [5, 10, 20, 60], marks = [], zoomStart = 40, extraTooltip } = {}) {
    const t = theme();
    const dates = rows.map((r) => r[0]);
    const ohlc = rows.map((r) => [r[1], r[2], r[3], r[4]]);
    const closes = rows.map((r) => r[2]);
    const vols = rows.map((r, i) => ({ value: r[5], itemStyle: { color: r[2] >= r[1] ? t.up : t.down, opacity: .7 } }));
    const maColors = ["#f59e0b", "#8b5cf6", "#3563e9", "#14b8a6"];
    const series = [{
      name: "K线", type: "candlestick", data: ohlc,
      itemStyle: { color: t.up, color0: t.down, borderColor: t.up, borderColor0: t.down },
      markPoint: { data: marks, animation: false },
    }];
    mas.forEach((n, i) => series.push({ name: `MA${n}`, type: "line", data: ma(closes, n), smooth: true, showSymbol: false,
      lineStyle: { width: 1.2, color: maColors[i % 4] }, itemStyle: { color: maColors[i % 4] } }));
    series.push({ name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols });
    chart(id).setOption(base({
      legend: { top: 0, data: mas.map((n) => `MA${n}`), textStyle: { color: t.text2, fontSize: 12 } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        ...base().tooltip,
        formatter: (ps) => {
          const i = ps[0].dataIndex, r = rows[i];
          const chg = i > 0 ? (r[2] / rows[i - 1][2] - 1) * 100 : null;
          let h = `<b>${r[0]}</b><br>开 ${r[1].toFixed(2)}　收 <span style="color:${chg >= 0 ? t.up : t.down}">${r[2].toFixed(2)} ${chg == null ? "" : pct(chg)}</span><br>高 ${r[4].toFixed(2)}　低 ${r[3].toFixed(2)}<br>量 ${money(r[5])}`;
          if (extraTooltip) h += extraTooltip(i, r);
          return h;
        },
      },
      grid: [{ left: 56, right: 24, top: 30, height: "62%" }, { left: 56, right: 24, top: "76%", height: "16%" }],
      xAxis: [axis("category", { data: dates, boundaryGap: true }),
        axis("category", { data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { show: false } })],
      yAxis: [axis("value", { scale: true }), axis("value", { gridIndex: 1, splitNumber: 2, axisLabel: { formatter: (v) => money(v, 0) } })],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: zoomStart, end: 100, zoomOnMouseWheel: "shift", moveOnMouseWheel: false },
        { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 16, start: zoomStart, end: 100, borderColor: t.line, textStyle: { color: t.text3 } }],
      series,
    }), true);
  }

  // ------------------------------------------------------------ 个股K线（主图叠加 + 多副图）
  const LINE_COLORS = ["#f59e0b", "#8b5cf6", "#3563e9", "#14b8a6", "#ec4899"];
  const SUB_DEFS = {
    VOL: { label: "成交量" }, MACD: { label: "MACD" }, KDJ: { label: "KDJ", ref: [20, 80] }, RSI: { label: "RSI", ref: [20, 80] },
    WR: { label: "WR", ref: [20, 80] }, CCI: { label: "CCI", ref: [-100, 100] }, BIAS: { label: "BIAS", ref: [0] },
    DMI: { label: "DMI" }, OBV: { label: "OBV" }, VR: { label: "VR", ref: [100] }, ATR: { label: "ATR" },
    TRIX: { label: "TRIX", ref: [0] }, DMA: { label: "DMA", ref: [0] }, ROC: { label: "ROC", ref: [0] },
    PSY: { label: "PSY", ref: [25, 75] }, MTM: { label: "MTM", ref: [0] },
  };
  const MAIN_OPTS = ["MA", "EMA", "BOLL", "无"];

  function stockChart(id, d, { main = "MA", subs = ["MACD"], marks = [], zoomStart = 30, extraTooltip } = {}) {
    const t = theme(), rows = d.kline;
    const dates = rows.map((r) => r[0]);
    const panels = ["VOL", ...subs];
    const top0 = 34, mainH = 300, panH = 92, gap = 26;
    const el = document.getElementById(id);
    el.style.height = `${top0 + mainH + panels.length * (panH + gap) + 40}px`;
    chart(id).resize();
    const grids = [{ left: 60, right: 24, top: top0, height: mainH }];
    panels.forEach((p, i) => grids.push({ left: 60, right: 24, top: top0 + mainH + gap + i * (panH + gap), height: panH }));
    const xAxis = grids.map((g, i) => axis("category", { data: dates, gridIndex: i, boundaryGap: true, axisLabel: { show: i === grids.length - 1, color: t.text3, fontSize: 11 } }));
    const yAxis = grids.map((g, i) => axis("value", { gridIndex: i, scale: true, splitNumber: i ? 2 : 5,
      axisLabel: { color: t.text3, fontSize: 10, formatter: i ? (v) => (Math.abs(v) >= 1e4 ? money(v, 0) : +v.toFixed(2)) : undefined } }));
    const series = [{ name: "K线", type: "candlestick", data: rows.map((r) => [r[1], r[2], r[3], r[4]]),
      itemStyle: { color: t.up, color0: t.down, borderColor: t.up, borderColor0: t.down }, markPoint: { data: marks, animation: false } }];
    const legend = [];
    if (main !== "无" && d.ta[main]) {
      Object.entries(d.ta[main]).forEach(([n, v], i) => {
        legend.push(n);
        series.push({ name: n, type: "line", data: v, showSymbol: false, smooth: true, lineStyle: { width: 1.2, color: LINE_COLORS[i], type: main === "BOLL" && n !== "MID" ? "dashed" : "solid" }, itemStyle: { color: LINE_COLORS[i] } });
      });
    }
    const titles = [];
    panels.forEach((p, k) => {
      const gi = k + 1;
      titles.push({ text: SUB_DEFS[p].label, left: 62, top: grids[gi].top - 18, textStyle: { fontSize: 11, color: t.text2, fontWeight: 600 } });
      if (p === "VOL") {
        series.push({ name: "成交量", type: "bar", xAxisIndex: gi, yAxisIndex: gi, data: rows.map((r) => ({ value: r[5], itemStyle: { color: r[2] >= r[1] ? t.up : t.down, opacity: .7 } })) });
        return;
      }
      const lines = d.ta[p] || {};
      Object.entries(lines).forEach(([n, v], i) => {
        if (p === "MACD" && n === "MACD") {
          series.push({ name: n, type: "bar", xAxisIndex: gi, yAxisIndex: gi, data: v.map((x) => ({ value: x, itemStyle: { color: x >= 0 ? t.up : t.down } })), barMaxWidth: 4 });
        } else {
          series.push({ name: n, type: "line", xAxisIndex: gi, yAxisIndex: gi, data: v, showSymbol: false, lineStyle: { width: 1.1, color: LINE_COLORS[i] }, itemStyle: { color: LINE_COLORS[i] },
            markLine: i === 0 && SUB_DEFS[p].ref ? { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: t.text3, type: "dotted" }, data: SUB_DEFS[p].ref.map((y) => ({ yAxis: y })) } : undefined });
        }
      });
    });
    const axisIdx = grids.map((_, i) => i);
    chart(id).setOption(base({
      title: titles,
      legend: { top: 0, data: legend, textStyle: { color: t.text2, fontSize: 12 } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: grids, xAxis, yAxis, series,
      tooltip: { ...base().tooltip, formatter: (ps) => {
        const i = ps[0].dataIndex, r = rows[i];
        const chg = r[7];
        let h = `<b>${r[0]}</b><br>开 ${r[1].toFixed(2)}　收 <span style="color:${chg >= 0 ? t.up : t.down}">${r[2].toFixed(2)} ${pct(chg)}</span><br>高 ${r[4].toFixed(2)}　低 ${r[3].toFixed(2)}　量 ${money(r[5])}`;
        if (extraTooltip) h += extraTooltip(i, r);
        const extra = ps.filter((p) => p.seriesName !== "K线" && p.seriesName !== "成交量" && isNum(p.value?.value ?? p.value));
        if (extra.length) h += "<br>" + extra.map((p) => `${p.marker}${p.seriesName} ${(+(p.value?.value ?? p.value)).toFixed(2)}`).join("　");
        return h;
      } },
      dataZoom: [{ type: "inside", xAxisIndex: axisIdx, start: zoomStart, end: 100, zoomOnMouseWheel: "shift", moveOnMouseWheel: false },
        { type: "slider", xAxisIndex: axisIdx, bottom: 6, height: 16, start: zoomStart, end: 100, borderColor: t.line, textStyle: { color: t.text3 } }],
    }), true);
  }

  // ------------------------------------------------------------ 表格
  /** columns: [{k, t, n(数字右对齐), f(v,row)=>html, w}] */
  function table(el, columns, rows, { onClick, sort, emptyText = "暂无数据", selKey } = {}) {
    el = typeof el === "string" ? document.getElementById(el) : el;
    let st = sort || null;
    function render() {
      let rs = rows.slice();
      if (st) {
        const { k, asc } = st;
        rs.sort((a, b) => {
          const x = a[k], y = b[k];
          if (x == null) return 1; if (y == null) return -1;
          return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "zh")) * (asc ? 1 : -1);
        });
      }
      if (!rs.length) { el.innerHTML = `<div class="empty">${emptyText}</div>`; return; }
      const head = columns.map((c) => `<th class="${c.n ? "n" : ""}" data-k="${c.k}">${c.t}${st && st.k === c.k ? `<span class="arr">${st.asc ? "↑" : "↓"}</span>` : ""}</th>`).join("");
      const body = rs.map((r, i) => `<tr class="${onClick ? "click" : ""}" data-i="${rows.indexOf(r)}">${columns.map((c) =>
        `<td class="${c.n ? "n" : ""}">${c.f ? c.f(r[c.k], r) : esc(r[c.k] ?? "—")}</td>`).join("")}</tr>`).join("");
      el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
      $$("th", el).forEach((th) => th.onclick = () => {
        const k = th.dataset.k;
        st = st && st.k === k ? { k, asc: !st.asc } : { k, asc: false };
        render();
      });
      if (onClick) $$("tbody tr", el).forEach((tr) => tr.onclick = () => {
        $$("tbody tr", el).forEach((x) => x.classList.remove("sel"));
        tr.classList.add("sel");
        onClick(rows[+tr.dataset.i]);
      });
    }
    render();
  }
  const stockLink = (v, r) => `<a class="tag" data-stock="${r.code}">${esc(v)}</a>`;
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-stock]");
    if (a) { e.stopPropagation(); closeDrawer(); go("stock", a.dataset.stock); }
  });

  // ------------------------------------------------------------ 路由
  const TITLES = { overview: "总览", limitup: "涨停复盘", lianban: "连板龙虎榜", themes: "题材热榜", boards: "板块资金", stock: "个股K线", watch: "盘中提示", flow: "资金排行", strategies: "策略中心", jobs: "任务与数据源" };
  const loaders = {};
  let current = "overview";
  function go(page, arg) {
    location.hash = arg ? `${page}/${arg}` : page;
  }
  function route() {
    const [page, arg] = (location.hash.slice(1) || "overview").split("/");
    current = TITLES[page] ? page : "overview";
    closeDrawer();
    $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === current));
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${current}`));
    $("#page-title").textContent = TITLES[current];
    loaders[current] && loaders[current](arg);
    setTimeout(() => Object.values(charts).forEach((c) => c.resize()), 30);
  }
  $$("#nav a").forEach((a) => a.onclick = () => go(a.dataset.page));
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));

  // ------------------------------------------------------------ 状态栏
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      $("#chip-date").textContent = `数据截至 ${s.last_date} · ${s.n_stocks}只`;
      $("#chip-archive").textContent = s.archive_days ? `归档 ${s.archive_days}天（${fmtDate(s.archive_first)}起）` : "归档 0天";
      const em = $("#chip-em");
      em.textContent = s.em_available ? "东财资金流 可用" : "东财资金流 限流中";
      em.className = "chip " + (s.em_available ? "ok" : "bad");
    } catch (e) {
      $("#chip-date").textContent = "后端未就绪：" + e.message;
    }
  }

  // ------------------------------------------------------------ 总览
  let ovData = null, idxSym = store.get("idx", "sh000001");
  const INDEX_LIST = [["sh000001", "上证"], ["sz399001", "深成"], ["sz399006", "创业板"], ["sh000688", "科创50"], ["sh000852", "中证1000"]];
  loaders.overview = async () => {
    if (!ovData) $("#kpis").innerHTML = `<div class="kpi"><div class="loading">正在计算市场指标…首次约需 10~30 秒</div></div>`;
    try { ovData = await api("/api/overview"); } catch (e) { $("#kpis").innerHTML = `<div class="kpi">加载失败：${esc(e.message)}</div>`; return; }
    renderOverview(ovData);
    loadIndex();
    loadMarketFlow();
  };

  function gauge(score) {
    const t = theme();
    const col = score >= 70 ? t.up : score >= 40 ? t.warn : t.down;
    return `<div class="gauge" style="background:conic-gradient(${col} ${score * 3.6}deg, ${t.line} 0);border-radius:50%">
      <div style="position:absolute;inset:8px;border-radius:50%;background:${t.panel}"></div><div class="gv" style="color:${col}">${Math.round(score)}</div></div>`;
  }
  function renderOverview(d) {
    const s = d.senti, t = theme();
    const posTxt = s.position >= 1 ? "满仓" : s.position > 0 ? "半仓" : "空仓";
    const mood = s.score >= 70 ? "高潮/强势" : s.score >= 40 ? "震荡/分歧" : "冰点/退潮";
    $("#kpis").innerHTML = `
      <div class="kpi hero" data-info="k_score">${gauge(s.score)}<div><div class="k">情绪分 · ${d.date}</div><div class="v">${mood}</div>
        <div class="s">策略仓位规则：<span class="pill ${s.position > 0 ? "acc" : "warn"}">${posTxt}</span></div></div></div>
      <div class="kpi" data-info="k_zt"><div class="k">涨停家数</div><div class="v up">${s.zt_count}</div><div class="s">最高 ${s.max_streak} 连板</div></div>
      <div class="kpi" data-info="k_dt"><div class="k">跌停家数</div><div class="v down">${s.dt_count}</div><div class="s">炸板率 ${pct01(s.zb_rate, 0)}</div></div>
      <div class="kpi" data-info="k_st"><div class="k">ST 涨停 / 跌停</div><div class="v"><span class="up">${s.st_zt_count}</span><span class="muted" style="font-size:16px"> / </span><span class="down">${s.st_dt_count}</span></div><div class="s">不计入情绪分</div></div>
      <div class="kpi" data-info="k_premium"><div class="k">昨涨停今日溢价</div><div class="v ${cls(s.premium)}">${pct01(s.premium, 2, true)}</div><div class="s">接力赚钱效应</div></div>
      <div class="kpi" data-info="k_updown"><div class="k">上涨 / 下跌</div><div class="v"><span class="up">${s.up}</span><span class="muted" style="font-size:16px"> / </span><span class="down">${s.down}</span></div>
        <div class="s">上涨占比 ${pct01(s.up / (s.up + s.down), 0)}</div></div>`;

    // 主线题材：同花顺涨停原因（按当时数据）/ 新浪板块（当前成分股）
    renderMainline(d);

    // 情绪周期
    const h = d.senti_hist;
    chart("ch-senti").setOption(base({
      grid: { left: 44, right: 44, top: 36, bottom: 28 },
      xAxis: axis("category", { data: h.dates }),
      yAxis: [axis("value", { name: "家数", nameTextStyle: { color: t.text3 } }), axis("value", { max: 100, min: 0, splitLine: { show: false }, name: "情绪分", nameTextStyle: { color: t.text3 } })],
      dataZoom: [{ type: "inside", start: 52, end: 100 }],
      series: [
        { name: "涨停", type: "bar", data: h.zt_count, itemStyle: { color: t.up, borderRadius: [2, 2, 0, 0] }, barMaxWidth: 6 },
        { name: "跌停", type: "bar", data: h.dt_count.map((v) => -v), itemStyle: { color: t.down, borderRadius: [0, 0, 2, 2] }, barMaxWidth: 6 },
        { name: "情绪分", type: "line", yAxisIndex: 1, data: h.score, showSymbol: false, lineStyle: { width: 1, color: t.accent, opacity: .35 }, itemStyle: { color: t.accent } },
        { name: "情绪分5日均", type: "line", yAxisIndex: 1, data: ma(h.score.map((v) => v ?? 0), 5), smooth: true, showSymbol: false,
          lineStyle: { width: 2.4, color: t.accent }, itemStyle: { color: t.accent },
          markLine: { silent: true, symbol: "none", lineStyle: { color: t.text3, type: "dashed" }, label: { color: t.text3, fontSize: 10, formatter: (p) => (p.value >= 70 ? "满仓线" : "空仓线") }, data: [{ yAxis: 40 }, { yAxis: 70 }] } },
      ],
    }), true);

    // 赚钱效应
    chart("ch-effect").setOption(base({
      grid: { left: 44, right: 44, top: 36, bottom: 28 },
      xAxis: axis("category", { data: h.dates }),
      yAxis: [axis("value", { name: "溢价", nameTextStyle: { color: t.text3 }, axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%", color: t.text3, fontSize: 11 } }),
        axis("value", { name: "炸板率", nameTextStyle: { color: t.text3 }, min: 0, max: 1, splitLine: { show: false }, axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%", color: t.text3, fontSize: 11 } })],
      dataZoom: [{ type: "inside", start: 52, end: 100 }],
      tooltip: { ...base().tooltip, formatter: (ps) => `<b>${ps[0].axisValue}</b><br>` + ps.map((p) => `${p.marker}${p.seriesName} ${pct01(p.value, 1, p.seriesName !== "炸板率")}`).join("<br>")
        + `<br>最高板 ${h.max_streak[ps[0].dataIndex]}` },
      series: [
        { name: "昨涨停溢价", type: "bar", data: h.premium.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? t.up : t.down } })), barMaxWidth: 6 },
        { name: "溢价5日均", type: "line", data: ma(h.premium.map((v) => v ?? 0), 5), smooth: true, showSymbol: false, lineStyle: { width: 2.2, color: t.accent }, itemStyle: { color: t.accent } },
        { name: "炸板率", type: "line", yAxisIndex: 1, data: ma(h.zb_rate.map((v) => v ?? 0), 3), smooth: true, showSymbol: false, lineStyle: { width: 1.4, color: t.warn, type: "dashed" }, itemStyle: { color: t.warn } },
      ],
    }), true);

    // 连板梯队
    const total = d.ladder.reduce((a, l) => a + l.count, 0);
    $("#ladder-hint").textContent = `${d.date} 共 ${total} 只涨停（本地日线计算）`;
    $("#ladder").innerHTML = d.ladder.map((l) => `<div class="lad-row"><div class="lad-n">${l.streak === 1 ? "首板" : l.streak + "板"}<div style="font-size:11px;font-weight:500">${l.count}只</div></div>
      <div class="lad-s">${l.stocks.map((s) => `<span class="tag ${s.st ? "st" : ""}" data-stock="${s.code}">${esc(s.name)}</span>`).join("")}${l.count > l.stocks.length ? `<span class="muted">…</span>` : ""}</div></div>`).join("") || `<div class="empty">今日无涨停</div>`;

    // 主线板块走势
    const bh = d.board_hist;
    chart("ch-boardhist").setOption(base({
      grid: { left: 36, right: 16, top: 50, bottom: 28 },
      legend: { top: 0, type: "scroll", textStyle: { color: t.text2, fontSize: 12 } },
      xAxis: axis("category", { data: bh.dates }),
      yAxis: axis("value", { minInterval: 1, name: "涨停家数", nameTextStyle: { color: t.text3 } }),
      tooltip: { ...base().tooltip, axisPointer: { type: "shadow" } },
      series: bh.series.map((s, i) => ({ name: s.name, type: "bar", stack: "b", data: s.data, barMaxWidth: 14,
        itemStyle: { borderRadius: i === bh.series.length - 1 ? [3, 3, 0, 0] : 0 } })),
    }), true);

    // 策略候选（可在多个策略间切换）
    renderCandTabs(d.tabs, d.active);
  }

  let mlSrc = store.get("mlSrc", "ths");
  $$("#ml-src button").forEach((b) => b.classList.toggle("on", b.dataset.v === mlSrc));
  bindSeg("ml-src", (v) => { mlSrc = v; store.set("mlSrc", v); if (ovData) renderMainline(ovData); });
  async function renderMainline(d) {
    const box = $("#mainline-list");
    if (mlSrc === "sina") {
      box.innerHTML = d.mainline.map((m) => `
        <div class="ml-item" data-board="${esc(m.board)}">
          <div class="nm">${esc(m.board)} ${m.is_main ? '<span class="pill up">主线</span>' : ""}</div>
          <div class="ct">3日 <b>${m.win}</b> · 今日 <b class="up">${m.today_zt}</b> 板</div>
          <div class="ns">${m.zt_names.length ? "涨停：" + m.zt_names.map(esc).join("、") : `成分 ${m.size} 只，今日无涨停`}</div>
        </div>`).join("") || `<div class="empty">暂无</div>`;
      $$("#mainline-list .ml-item").forEach((el) => el.onclick = () => openBoard(el.dataset.board));
      return;
    }
    box.innerHTML = `<div class="loading">加载同花顺题材…</div>`;
    let t;
    try { t = await api("/api/themes"); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (mlSrc !== "ths") return;
    const rows = (t.rank || []).filter((r) => !r.generic).slice(0, 12);
    box.innerHTML = rows.map((r) => `
      <div class="ml-item" data-tag="${esc(r.tag)}">
        <div class="nm">${esc(r.tag)} ${r.is_main ? '<span class="pill up">主线</span>' : ""}</div>
        <div class="ct">3日 <b>${r.strength}</b> · 今日 <b class="up">${r.today}</b> 板</div>
        <div class="ns">${r.stocks.length ? "涨停：" + r.stocks.map((x) => esc(x.name) + (x.boards > 1 ? `(${x.boards}板)` : "")).join("、") : "今日无涨停"}</div>
      </div>`).join("") || `<div class="empty">${esc(t.error || "暂无题材数据")}</div>`;
    $$("#mainline-list .ml-item").forEach((el) => el.onclick = () => go("themes"));
  }

  let candTab = store.get("candTab", "");
  let stItemsCache = null;
  api("/api/strategies").then((d) => { stItemsCache = d.items; }).catch(() => {});
  function renderCandTabs(tabs, active) {
    if (!tabs.find((t) => t.key === candTab)) candTab = active;
    const label = (t) => (t.active ? "每日策略" : t.mode === "rebalance" ? (t.universe === "etf" ? "ETF轮动" : "调仓") : "短线");
    $("#cand-tabs").innerHTML = tabs.map((t) => `<button data-v="${t.key}" class="${t.key === candTab ? "on" : ""}" title="${esc(t.name)}">${esc(label(t))} · ${esc(t.name.replace(/（.*?）/g, ""))}</button>`).join("")
      + `<button data-v="__more" title="在策略中心勾选更多策略">＋</button>`;
    $$("#cand-tabs button").forEach((b) => b.onclick = () => {
      if (b.dataset.v === "__more") { location.hash = "strategies"; return; }
      candTab = b.dataset.v; store.set("candTab", candTab);
      renderCandTabs(tabs, active);
    });
    loadCandidates(candTab);
  }

  async function loadCandidates(key) {
    $("#cand").innerHTML = `<div class="loading">计算中…</div>`;
    let d;
    try { d = await api(`/api/candidates?key=${encodeURIComponent(key)}`); }
    catch (e) { $("#cand").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (key !== candTab) return;
    const sInfo = (stItemsCache || []).find((x) => x.key === key);
    window.INFO_DYNAMIC.cand = `<b>${esc(d.strategy.name)}</b>${d.strategy.active ? "（每日策略，生成计划/自动下单用它）" : "（仅查看）"}<br>${esc(sInfo ? sInfo.description : "")}`
      + (d.strategy.mode === "rebalance"
        ? "<br><br>定期调仓：在调仓日收盘后算出目标持仓，次日开盘先卖「调出」再买「新调入」，继续持有的不动。调仓以来 = 决策日收盘至今的涨跌（复权）。"
        : `<br><br>信号日 T 收盘后选出，T+1 执行：挂单价 = T 日收盘 ×（1${d.params.buy_offset >= 0 ? "+" : ""}${(d.params.buy_offset * 100).toFixed(0)}%），开盘就低于挂单价按开盘价成交；高开超过 ${(d.params.max_gap_up * 100).toFixed(0)}% 放弃。得分越高越优先，只买空余仓位数量的前几只。`);
    if (d.strategy.mode === "rebalance") return renderRebalance(d);
    const p = d.params;
    $("#cand-title").innerHTML = `策略候选 · ${esc(d.strategy.name)}`;
    $("#cand-hint").textContent = `次日挂 ${pct(p.buy_offset * 100, 0)}，高开>${pct(p.max_gap_up * 100, 0, false)} 放弃，止盈 ${pct(p.take_profit * 100, 0)} / 止损 ${pct(p.stop_loss * 100, 0)} / 最多 ${p.max_hold_days} 天`;
    const wrap = $("#cand");
    if (d.candidates.length) {
      wrap.innerHTML = `<div class="table-wrap" id="cand-t"></div>`;
      table("cand-t", [
        { k: "name", t: "股票", f: stockLink }, { k: "boards", t: "主线板块" },
        { k: "max_streak", t: "连板", n: 1 }, { k: "ret", t: "今日", n: 1, f: (v) => `<span class="${cls(v)}">${pct01(v, 2, true)}</span>` },
        { k: "score", t: "得分", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") }, { k: "buy_price", t: "挂单价", n: 1, f: (v) => v.toFixed(2) },
      ], d.candidates);
    } else {
      wrap.innerHTML = `<div class="empty">今日没有符合「${esc(d.strategy.name)}」条件的标的</div>
        <div class="sub-h" style="margin-top:6px">近60日信号</div><div class="table-wrap" id="cand-recent"></div>`;
      table("cand-recent", [{ k: "date", t: "信号日" }, { k: "name", t: "股票", f: stockLink }, { k: "score", t: "得分", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") }, { k: "close", t: "收盘", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") }],
        d.recent_signals.slice().reverse(), { emptyText: "近60日无信号" });
    }
  }

  function renderRebalance(d) {
    const rb = d.rebalance || {};
    $("#cand-title").innerHTML = `目标持仓 · ${esc(d.strategy.name)}`;
    $("#cand-hint").textContent = rb.decision_date
      ? (rb.is_today ? `今天 ${rb.decision_date} 是调仓日，明天开盘执行` : `最近调仓决策：${rb.decision_date}（今天不调仓，继续持有）`) : "";
    $("#cand").innerHTML = `<div class="table-wrap" id="cand-t"></div><div class="hint" style="margin-top:8px">调仓类策略：先卖「调出」，再按权重买「新调入」，100股整数倍；自动下单暂不支持，请按清单手动操作</div>`;
    const tag = { "新调入": "up", "继续持有": "acc", "调出": "down" };
    table("cand-t", [
      { k: "name", t: "品种", f: stockLink }, { k: "action", t: "动作", f: (v) => `<span class="pill ${tag[v] || ""}">${v}</span>` },
      { k: "weight", t: "目标权重", n: 1, f: (v) => (v ? (v * 100).toFixed(1) + "%" : "—") },
      { k: "close", t: "最新价", n: 1, f: (v) => (isNum(v) ? v.toFixed(3) : "—") },
      { k: "since", t: "调仓以来", n: 1, f: (v) => (isNum(v) ? `<span class="${cls(v)}">${pct01(v, 2, true)}</span>` : "—") },
    ], rb.rows || [], { emptyText: "还没有调仓决策" });
  }

  async function loadIndex() {
    $("#index-seg").innerHTML = INDEX_LIST.map(([s, n]) => `<button data-v="${s}" class="${s === idxSym ? "on" : ""}">${n}</button>`).join("");
    $$("#index-seg button").forEach((b) => b.onclick = () => { idxSym = b.dataset.v; store.set("idx", idxSym); loadIndex(); });
    try {
      const d = await api(`/api/index/${idxSym}?days=250`);
      kline("ch-index", d.rows, { mas: [5, 20, 60], zoomStart: 45 });
    } catch (e) { emptyChart("ch-index", "指数获取失败：" + e.message); }
  }

  async function loadMarketFlow() {
    const t = theme();
    try {
      const d = await api("/api/market_flow");
      if (!d.available) return emptyChart("ch-mflow", d.msg);
      const r = d.rows;
      chart("ch-mflow").setOption(base({
        grid: { left: 56, right: 16, top: 36, bottom: 28 },
        xAxis: axis("category", { data: r.map((x) => x.date) }),
        yAxis: axis("value", { axisLabel: { formatter: (v) => money(v, 0), color: t.text3, fontSize: 11 } }),
        tooltip: { ...base().tooltip, valueFormatter: (v) => money(v) },
        dataZoom: [{ type: "inside", start: 40, end: 100 }],
        series: [
          ...[["super", "超大单"], ["large", "大单"], ["mid", "中单"], ["small", "小单"]].map(([k, n], i) => ({ name: n, type: "bar", stack: "f", data: r.map((x) => x[k]), barMaxWidth: 10, itemStyle: { opacity: .85 } })),
          { name: "主力净流入", type: "line", data: r.map((x) => x.main), showSymbol: false, smooth: true, lineStyle: { width: 2, color: t.up }, itemStyle: { color: t.up } },
        ],
      }), true);
    } catch (e) { emptyChart("ch-mflow", "获取失败：" + e.message); }
    finally { loadStatus(); }  // 东财可用状态可能刚变化
  }

  // ------------------------------------------------------------ 板块抽屉（主线板块成分）
  function openDrawer(title, html) {
    $("#drawer-title").textContent = title;
    $("#drawer-body").innerHTML = html;
    $("#drawer").classList.add("open");
    $("#drawer-mask").style.display = "block";
  }
  function closeDrawer() {
    $("#drawer").classList.remove("open");
    $("#drawer-mask").style.display = "none";
  }
  $("#drawer-close").onclick = closeDrawer;
  $("#drawer-mask").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

  async function openBoard(name) {
    openDrawer(name, `<div class="loading">加载中…</div>`);
    try {
      const d = await api(`/api/mainline_board?name=${encodeURIComponent(name)}`);
      $("#drawer-body").innerHTML = `<div class="sub-h">近60日板块涨停家数</div><div class="chart h200" id="ch-drawer" style="height:200px"></div>
        <div class="sub-h" style="margin-top:12px">成分股今日表现（${d.members.length}只，新浪板块口径）</div><div class="table-wrap" id="drawer-t" style="max-height:calc(100vh - 380px)"></div>`;
      const t = theme();
      chart("ch-drawer").setOption(base({ grid: { left: 30, right: 10, top: 10, bottom: 24 }, legend: { show: false },
        xAxis: axis("category", { data: d.zt_hist.dates }), yAxis: axis("value", { minInterval: 1 }),
        series: [{ type: "bar", name: "涨停", data: d.zt_hist.data, itemStyle: { color: t.up, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 10 }] }), true);
      table("drawer-t", [
        { k: "name", t: "股票", f: (v, r) => stockLink(v, r) + (r.leader ? ' <span class="pill up">龙头</span>' : "") },
        { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
        { k: "streak", t: "连板", n: 1, f: (v) => (v ? v + "板" : "—") },
        { k: "amount", t: "成交额", n: 1, f: (v) => money(v) },
      ], d.members);
    } catch (e) { $("#drawer-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------ 涨停复盘
  let luDate = null;
  loaders.limitup = async (arg) => {
    if (arg) luDate = arg;
    let d;
    try { d = await api("/api/limitup" + (luDate ? `?date=${luDate}` : "")); } catch (e) { $("#lu-kpis").innerHTML = esc(e.message); return; }
    luDate = d.date.replace(/-/g, "");
    $("#lu-source").textContent = `数据来源：${d.source || "无"}`;
    $("#lu-dates").innerHTML = d.dates.slice().reverse().map((x) => `<button class="${x === luDate ? "on" : ""}" data-v="${x}">${fmtDate(x).slice(5)}</button>`).join("")
      || `<span class="hint">还没有归档数据，点右上角「同步数据」开始积累</span>`;
    $$("#lu-dates button").forEach((b) => b.onclick = () => { luDate = b.dataset.v; loaders.limitup(); });

    const zt = d.zt || [];
    const seal = zt.length + (d.zb_count || 0) ? zt.length / (zt.length + (d.zb_count || 0)) : null;
    const maxS = zt.reduce((m, r) => Math.max(m, r.streak || 0), 0);
    $("#lu-kpis").innerHTML = `
      <div class="kpi" data-info="lu_kpi"><div class="k">涨停</div><div class="v up">${zt.length}</div><div class="s">最高 ${maxS} 板</div></div>
      <div class="kpi" data-info="lu_kpi"><div class="k">炸板</div><div class="v">${d.zb_count ?? "—"}</div><div class="s">封板率 ${pct01(seal, 0)}</div></div>
      <div class="kpi" data-info="lu_kpi"><div class="k">跌停</div><div class="v down">${d.dt_count ?? "—"}</div></div>
      <div class="kpi" data-info="lu_kpi"><div class="k">昨涨停今日均涨</div><div class="v ${cls(d.prev_premium)}">${isNum(d.prev_premium) ? pct(d.prev_premium) : "—"}</div>
        <div class="s">上涨比例 ${pct01(d.prev_up_ratio, 0)}</div></div>
      <div class="kpi" data-info="lu_kpi"><div class="k">连板股（≥2板）</div><div class="v">${zt.filter((r) => r.streak >= 2).length}</div></div>`;

    const t = theme();
    const ind = (d.industry || []).slice().reverse();
    if (ind.length) {
      chart("ch-lu-ind").setOption(base({ grid: { left: 90, right: 30, top: 10, bottom: 20 }, legend: { show: false }, tooltip: { trigger: "item" },
        xAxis: axis("value", { minInterval: 1 }), yAxis: axis("category", { data: ind.map((x) => x.name), axisLabel: { color: t.text2, fontSize: 12 } }),
        series: [{ type: "bar", data: ind.map((x) => x.value), barMaxWidth: 14, itemStyle: { color: t.up, borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: "right", color: t.text2, fontSize: 11 } }] }), true);
    } else emptyChart("ch-lu-ind", "该日无归档，无行业分布");

    const cols = [
      { k: "name", t: "股票", f: stockLink }, { k: "streak", t: "连板", n: 1, f: (v) => `<b class="up">${v}</b>` },
      { k: "stat", t: "涨停统计" }, { k: "first_time", t: "首封", f: hhmm }, { k: "last_time", t: "末封", f: hhmm },
      { k: "breaks", t: "炸板", n: 1 }, { k: "seal", t: "封单", n: 1, f: (v) => money(v) },
      { k: "amount", t: "成交额", n: 1, f: (v) => money(v) }, { k: "turnover", t: "换手", n: 1, f: (v) => (isNum(v) ? v.toFixed(1) + "%" : "—") },
      { k: "float_mv", t: "流通市值", n: 1, f: (v) => money(v, 0) }, { k: "industry", t: "行业" },
    ];
    const drawZt = () => {
      const q = $("#lu-filter").value.trim();
      table("lu-table", cols, q ? zt.filter((r) => (r.name + (r.industry || "")).includes(q)) : zt);
    };
    $("#lu-filter").oninput = drawZt;
    drawZt();
    table("lu-zb", [{ k: "name", t: "股票", f: stockLink }, { k: "pct", t: "收盘涨幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
      { k: "breaks", t: "炸板次数", n: 1 }, { k: "industry", t: "行业" }], d.zb || [], { emptyText: "无归档数据" });
    table("lu-dt", [{ k: "name", t: "股票", f: stockLink }, { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
      { k: "days", t: "连续跌停", n: 1 }, { k: "industry", t: "行业" }], d.dt || [], { emptyText: "无归档数据" });
  };

  // ------------------------------------------------------------ 板块资金
  let bkKind = "concept", bkPeriod = "即时", bkRows = [];
  function bindSeg(id, cb) {
    $$(`#${id} button`).forEach((b) => b.onclick = () => {
      $$(`#${id} button`).forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      cb(b.dataset.v);
    });
  }
  bindSeg("bk-kind", (v) => { bkKind = v; loaders.boards(); });
  bindSeg("bk-period", (v) => { bkPeriod = v; loaders.boards(); });
  loaders.boards = async () => {
    const t = theme();
    try { bkRows = await api(`/api/boards?kind=${bkKind}&period=${encodeURIComponent(bkPeriod)}`); }
    catch (e) { emptyChart("ch-bk-rank", e.message); return; }
    const sorted = bkRows.slice().sort((a, b) => b.net - a.net);
    const top = sorted.slice(0, 12), bot = sorted.slice(-12);
    const both = [...top, ...bot.filter((x) => !top.includes(x))];
    chart("ch-bk-rank").setOption(base({
      grid: { left: 56, right: 16, top: 36, bottom: 70 }, legend: { show: false },
      tooltip: { trigger: "axis", backgroundColor: t.panel, borderColor: t.line, textStyle: { color: t.text },
        formatter: (ps) => { const r = both[ps[0].dataIndex]; return `<b>${esc(r.name)}</b><br>净流入 ${money(r.net)}<br>涨跌幅 ${pct(r.pct)}${r.leader ? `<br>领涨 ${esc(r.leader)} ${pct(r.leader_pct)}` : ""}`; } },
      title: { text: `${bkKind === "concept" ? "概念" : "行业"}板块 资金净流入 前12 / 后12（同花顺口径，${bkPeriod === "即时" ? "今日" : bkPeriod.replace("排行", "")}）`, left: 0, top: 0, textStyle: { fontSize: 13, color: t.text2, fontWeight: 500 } },
      xAxis: axis("category", { data: both.map((r) => r.name), axisLabel: { rotate: 40, color: t.text2, fontSize: 11, interval: 0 } }),
      yAxis: axis("value", { axisLabel: { formatter: (v) => money(v, 0), color: t.text3, fontSize: 11 } }),
      series: [{ type: "bar", data: both.map((r) => ({ value: r.net, itemStyle: { color: r.net >= 0 ? t.up : t.down, borderRadius: r.net >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } })), barMaxWidth: 22 }],
    }), true);
    chart("ch-bk-rank").off("click");
    chart("ch-bk-rank").on("click", (p) => showBoardK(both[p.dataIndex].name));
    drawBoardTable();
    if (!$("#bk-k-title").dataset.name) showBoardK(sorted[0].name);
  };
  function drawBoardTable() {
    const q = $("#bk-filter").value.trim();
    table("bk-table", [
      { k: "name", t: "板块" }, { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
      { k: "net", t: "净流入", n: 1, f: (v) => `<span class="${cls(v)}">${money(v)}</span>` },
      { k: "inflow", t: "流入", n: 1, f: (v) => money(v) }, { k: "outflow", t: "流出", n: 1, f: (v) => money(v) },
      { k: "count", t: "家数", n: 1 }, { k: "leader", t: "领涨股", f: (v, r) => (v ? `${esc(v)} <span class="${cls(r.leader_pct)}">${pct(r.leader_pct)}</span>` : "—") },
    ], q ? bkRows.filter((r) => r.name.includes(q)) : bkRows, { onClick: (r) => showBoardK(r.name), sort: { k: "net", asc: false } });
  }
  $("#bk-filter").oninput = drawBoardTable;
  async function showBoardK(name) {
    $("#bk-k-title").textContent = `${name} · 板块指数`;
    $("#bk-k-title").dataset.name = name;
    try {
      const d = await api(`/api/board_index?name=${encodeURIComponent(name)}&kind=${bkKind}`);
      kline("ch-bk-k", d.rows, { mas: [5, 20], zoomStart: 40 });
    } catch (e) { emptyChart("ch-bk-k", e.message); }
  }

  // ------------------------------------------------------------ 个股
  let stockCode = store.get("stock", "600838"), stockDays = 250;
  bindSeg("stock-days", (v) => { stockDays = +v; loadStock(stockCode); });
  loaders.stock = (arg) => loadStock(arg || stockCode);
  const qIn = $("#stock-q"), sug = $("#stock-suggest");
  let sugTimer, sugItems = [];
  qIn.oninput = () => {
    clearTimeout(sugTimer);
    sugTimer = setTimeout(async () => {
      const q = qIn.value.trim();
      if (!q) { sug.style.display = "none"; return; }
      sugItems = await api(`/api/search?q=${encodeURIComponent(q)}`);
      sug.innerHTML = sugItems.map((s, i) => `<div class="${i === 0 ? "on" : ""}" data-c="${s.code}"><span>${esc(s.name)}</span><span class="muted num">${s.code}</span></div>`).join("") || `<div class="muted">无匹配</div>`;
      sug.style.display = "block";
      $$("div[data-c]", sug).forEach((el) => el.onmousedown = () => pick(el.dataset.c));
    }, 150);
  };
  qIn.onkeydown = (e) => { if (e.key === "Enter" && sugItems[0]) pick(sugItems[0].code); };
  qIn.onblur = () => setTimeout(() => (sug.style.display = "none"), 150);
  function pick(code) { sug.style.display = "none"; qIn.value = ""; go("stock", code); }

  async function loadStock(code) {
    stockCode = code; store.set("stock", code);
    const t = theme();
    $("#stock-info").innerHTML = `<span class="muted">加载 ${esc(code)}…</span>`;
    let d;
    try { d = await api(`/api/stock/${code}?days=${stockDays}`); }
    catch (e) { $("#stock-info").innerHTML = `<span class="down">${esc(e.message)}</span>`; return; }
    stockData = d;
    const last = d.kline[d.kline.length - 1], chg = last[7];
    $("#stock-info").innerHTML = `<span class="nm">${esc(d.name)}</span><span class="muted num">${d.code}</span>
      ${d.is_st ? `<span class="pill warn">ST · 涨跌幅${Math.round(d.limit * 100)}%</span>` : ""}
      <span class="px ${cls(chg)} num" id="q-px">${last[2].toFixed(2)}</span><span class="${cls(chg)} num" id="q-pct">${pct(chg)}</span>
      <span class="muted" id="q-time">${last[0]} 收盘</span>
      ${d.streak > 0 ? `<span class="pill up">${d.streak}连板</span>` : ""}
      <span class="muted">流通市值 ${money(d.float_mv, 0)} · 换手 ${isNum(last[8]) ? last[8].toFixed(2) + "%" : "—"}</span>
      ${d.boards.slice(0, 6).map((b) => `<span class="pill acc">${esc(b)}</span>`).join("")}`;
    drawStock();
    loadStockFlow(code);
    pollQuote();
  }

  // 指标选择（记住上次的选择）
  let stockData = null;
  let taMain = store.get("taMain", "MA");
  let taSubs = (store.get("taSubs", "MACD,KDJ") || "").split(",").filter((x) => SUB_DEFS[x] && x !== "VOL");
  function renderTaControls() {
    $("#ta-main").innerHTML = MAIN_OPTS.map((m) => `<button data-v="${m}" class="${m === taMain ? "on" : ""}">${m}</button>`).join("");
    $$("#ta-main button").forEach((b) => b.onclick = () => { taMain = b.dataset.v; store.set("taMain", taMain); renderTaControls(); drawStock(); });
    $("#ta-subs").innerHTML = Object.keys(SUB_DEFS).filter((k) => k !== "VOL")
      .map((k) => `<button data-v="${k}" class="${taSubs.includes(k) ? "on" : ""}">${SUB_DEFS[k].label}</button>`).join("");
    $$("#ta-subs button").forEach((b) => b.onclick = () => {
      const k = b.dataset.v;
      taSubs = taSubs.includes(k) ? taSubs.filter((x) => x !== k) : [...taSubs, k].slice(-3);
      store.set("taSubs", taSubs.join(","));
      renderTaControls(); drawStock();
    });
  }
  renderTaControls();

  function drawStock() {
    const d = stockData;
    if (!d) return;
    const t = theme();
    const idx = Object.fromEntries(d.kline.map((r, i) => [r[0], i]));
    const marks = [];
    d.zt_dates.forEach((dt) => { const i = idx[dt]; if (i != null) marks.push({ coord: [dt, d.kline[i][4]], symbol: "triangle", symbolSize: 8, symbolOffset: [0, -8], itemStyle: { color: t.up }, label: { show: false } }); });
    d.signals.forEach((dt) => { const i = idx[dt]; if (i != null) marks.push({ coord: [dt, d.kline[i][3]], symbol: "diamond", symbolSize: 12, symbolOffset: [0, 12], itemStyle: { color: t.accent }, label: { show: false } }); });
    const lhbMap = {};
    d.lhb.forEach((x) => { const i = idx[x.date]; lhbMap[x.date] = x; if (i != null) marks.push({ name: "lhb", value: x.date, coord: [x.date, d.kline[i][3]], symbol: "circle", symbolSize: 9, symbolOffset: [0, 24], itemStyle: { color: x.net_buy >= 0 ? t.up : t.down }, label: { show: false } }); });
    stockChart("ch-stock", d, {
      main: taMain, subs: taSubs, marks, zoomStart: stockDays > 250 ? 60 : 30,
      extraTooltip: (i, r) => `<br>换手 ${isNum(r[8]) ? r[8].toFixed(2) + "%" : "—"}　额 ${money(r[6])}` +
        (lhbMap[r[0]] ? `<br>龙虎榜净买 <b style="color:${lhbMap[r[0]].net_buy >= 0 ? t.up : t.down}">${money(lhbMap[r[0]].net_buy)}</b> · ${esc((lhbMap[r[0]].reason || "").slice(0, 24))}` : ""),
    });
    const c = chart("ch-stock");
    c.off("click");
    c.on("click", (p) => {
      if (p.componentType === "markPoint" && p.data && p.data.name === "lhb") openSeats(d.code, p.data.value, d.name);
    });
  }

  // 龙虎榜营业部席位
  async function openSeats(code, date, name) {
    openDrawer(`${name || code} · ${date} 龙虎榜`, `<div class="loading">加载营业部席位…</div>`);
    try {
      const d = await api(`/api/lhb_seats?code=${code}&date=${date}`);
      const reasons = [...new Set(d.seats.map((x) => x.reason))];
      const jg = d.jg[0];
      let html = jg ? `<div class="kpis" style="grid-template-columns:repeat(3,1fr)">
          <div class="kpi"><div class="k">买方机构</div><div class="v up">${jg.jg_buyers}</div></div>
          <div class="kpi"><div class="k">卖方机构</div><div class="v down">${jg.jg_sellers}</div></div>
          <div class="kpi"><div class="k">机构净买</div><div class="v ${cls(jg.jg_net)}">${money(jg.jg_net)}</div></div></div>` : "";
      reasons.forEach((rs, ri) => {
        html += `<div class="sub-h" style="margin-top:10px">${esc(rs || "上榜")}</div>`;
        ["买", "卖"].forEach((side) => { html += `<div class="hint" style="margin:6px 0 4px">${side}方前5</div><div class="table-wrap" id="seat-${ri}-${side}"></div>`; });
      });
      $("#drawer-body").innerHTML = html || `<div class="empty">没有席位数据</div>`;
      reasons.forEach((rs, ri) => ["买", "卖"].forEach((side) => {
        table(`seat-${ri}-${side}`, [
          { k: "seat", t: "营业部", f: (v) => `<span title="${esc(v)}">${esc(v.length > 22 ? v.slice(0, 22) + "…" : v)}</span>` },
          { k: "buy", t: "买入", n: 1, f: (v) => money(v) }, { k: "sell", t: "卖出", n: 1, f: (v) => money(v) },
          { k: "net", t: "净额", n: 1, f: (v) => `<b class="${cls(v)}">${money(v)}</b>` },
        ], d.seats.filter((x) => x.reason === rs && x.side === side));
      }));
    } catch (e) { $("#drawer-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // 盘中实时价（腾讯行情，交易时段每15秒刷新）
  let quoteTimer = null;
  async function pollQuote() {
    clearTimeout(quoteTimer);
    if (current !== "stock" || !stockData) return;
    const now = new Date(), hm = now.getHours() * 100 + now.getMinutes(), wd = now.getDay();
    const trading = wd >= 1 && wd <= 5 && ((hm >= 915 && hm <= 1131) || (hm >= 1259 && hm <= 1501));
    if (!trading) return;
    try {
      const q = await api(`/api/quote/${stockData.code}`);
      if (q.price) {
        $("#q-px").textContent = q.price.toFixed(2); $("#q-px").className = `px num ${cls(q.pct)}`;
        $("#q-pct").textContent = pct(q.pct); $("#q-pct").className = `num ${cls(q.pct)}`;
        $("#q-time").textContent = `实时 ${q.time.slice(8, 10)}:${q.time.slice(10, 12)}:${q.time.slice(12, 14)}`;
      }
    } catch { /* 忽略 */ }
    quoteTimer = setTimeout(pollQuote, 15000);
  }

  async function loadStockFlow(code) {
    const t = theme();
    $("#flow-hint").textContent = "加载中…";
    emptyChart("ch-stock-flow", "资金流加载中…");
    let f;
    try { f = await api(`/api/stock_flow/${code}`); } catch (e) { emptyChart("ch-stock-flow", e.message); return; }
    if (code !== stockCode) return;
    if (f.flow_em && f.flow_em.length) {
      const r = f.flow_em;
      $("#flow-hint").textContent = `东方财富 · 主力=超大单+大单 · ${r.length}个交易日`;
      chart("ch-stock-flow").setOption(base({
        grid: { left: 60, right: 50, top: 36, bottom: 28 },
        xAxis: axis("category", { data: r.map((x) => x.date) }),
        yAxis: [axis("value", { axisLabel: { formatter: (v) => money(v, 0), color: t.text3, fontSize: 11 } }),
          axis("value", { splitLine: { show: false }, axisLabel: { formatter: (v) => v.toFixed(0) + "%", color: t.text3, fontSize: 11 } })],
        tooltip: { ...base().tooltip, formatter: (ps) => `<b>${ps[0].axisValue}</b><br>` + ps.map((p) => `${p.marker}${p.seriesName} ${p.seriesName.includes("占比") ? pct(p.value) : money(p.value)}`).join("<br>") },
        dataZoom: [{ type: "inside", start: 50, end: 100 }],
        series: [
          { name: "超大单", type: "bar", stack: "f", data: r.map((x) => x.super), itemStyle: { color: "#b91c1c" }, barMaxWidth: 10 },
          { name: "大单", type: "bar", stack: "f", data: r.map((x) => x.large), itemStyle: { color: "#f87171" } },
          { name: "中单", type: "bar", stack: "f", data: r.map((x) => x.mid), itemStyle: { color: "#93c5fd" } },
          { name: "小单", type: "bar", stack: "f", data: r.map((x) => x.small), itemStyle: { color: "#64748b" } },
          { name: "主力净占比", type: "line", yAxisIndex: 1, data: r.map((x) => x.main_pct), showSymbol: false, smooth: true, lineStyle: { width: 1.6, color: t.accent }, itemStyle: { color: t.accent } },
        ],
      }), true);
    } else if (f.flow_ths && f.flow_ths.length) {
      const r = f.flow_ths;
      $("#flow-hint").textContent = `同花顺每日归档 · 已积累 ${r.length} 天（东财${f.em_available ? "无数据" : "限流中"}，暂无大小单拆分）`;
      chart("ch-stock-flow").setOption(base({
        grid: { left: 60, right: 20, top: 24, bottom: 28 }, legend: { show: false },
        xAxis: axis("category", { data: r.map((x) => x.date) }),
        yAxis: axis("value", { axisLabel: { formatter: (v) => money(v, 0), color: t.text3, fontSize: 11 } }),
        tooltip: { ...base().tooltip, axisPointer: { type: "shadow" },
          formatter: (ps) => { const x = r[ps[0].dataIndex]; return `<b>${x.date}</b><br>净流入 <b style="color:${x.net >= 0 ? t.up : t.down}">${money(x.net)}</b><br>流入 ${money(x.inflow)}　流出 ${money(x.outflow)}`; } },
        series: [{ name: "净流入", type: "bar", data: r.map((x) => ({ value: x.net, itemStyle: { color: x.net >= 0 ? t.up : t.down, borderRadius: 3 } })), barMaxWidth: 24 }],
      }), true);
    } else {
      $("#flow-hint").textContent = "";
      emptyChart("ch-stock-flow", f.em_available ? "暂无资金流数据" : "东方财富资金流接口限流中，同花顺归档还没有这只股票的数据（每日同步后积累）");
    }
  }

  // ------------------------------------------------------------ 资金排行
  let flowDate = null;
  loaders.flow = async () => {
    let d;
    try { d = await api("/api/stock_flow_rank" + (flowDate ? `?date=${flowDate}` : "")); } catch (e) { $("#flow-in").innerHTML = esc(e.message); return; }
    const sel = $("#flow-date");
    sel.innerHTML = d.dates.slice().reverse().map((x) => `<option value="${x}">${fmtDate(x)}</option>`).join("") || `<option>无归档</option>`;
    if (d.date) sel.value = d.date.replace(/-/g, "");
    sel.onchange = () => { flowDate = sel.value; loaders.flow(); };
    const cols = [{ k: "name", t: "股票", f: stockLink }, { k: "price", t: "价格", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") },
      { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
      { k: "net", t: "净额", n: 1, f: (v) => `<b class="${cls(v)}">${money(v)}</b>` },
      { k: "inflow", t: "流入", n: 1, f: (v) => money(v) }, { k: "outflow", t: "流出", n: 1, f: (v) => money(v) }, { k: "amount", t: "成交额", n: 1, f: (v) => money(v) }];
    table("flow-in", cols, d.inflow, { emptyText: "还没有归档资金流，点「同步数据」后积累" });
    table("flow-out", cols, d.outflow, { emptyText: "还没有归档资金流" });
  };

  // ------------------------------------------------------------ 连板龙虎榜
  let lbDate = null, lbMin = 2;
  bindSeg("lb-min", (v) => { lbMin = +v; loaders.lianban(); });
  loaders.lianban = async (arg) => {
    if (arg) lbDate = arg;
    let d;
    try { d = await api(`/api/lianban?min=${lbMin}` + (lbDate ? `&date=${lbDate}` : "")); }
    catch (e) { $("#lb-kpis").innerHTML = `<div class="kpi" data-info="lb_kpi">${esc(e.message)}</div>`; return; }
    const sel = $("#lb-date");
    sel.innerHTML = d.dates.slice().reverse().map((x) => `<option value="${x}">${x}</option>`).join("");
    sel.value = d.date;
    sel.onchange = () => { lbDate = sel.value; loaders.lianban(); };
    const rows = d.rows, t = theme();
    const onLhb = rows.filter((r) => r.reason), jgBuy = rows.filter((r) => r.jg_net > 0);
    $("#lb-kpis").innerHTML = `
      <div class="kpi"><div class="k">≥${d.min}连板</div><div class="v up">${rows.length}</div><div class="s">其中ST ${rows.filter((r) => r.is_st).length} 只</div></div>
      <div class="kpi"><div class="k">最高板</div><div class="v">${rows.length ? rows[0].streak : 0}</div><div class="s">${rows.length ? esc(rows[0].name) : "—"}</div></div>
      <div class="kpi"><div class="k">上龙虎榜</div><div class="v">${onLhb.length}</div><div class="s">净买入 ${onLhb.filter((r) => r.net_buy > 0).length} 只</div></div>
      <div class="kpi"><div class="k">机构净买入</div><div class="v up">${jgBuy.length}</div></div>
      <div class="kpi"><div class="k">ST 涨停 / 跌停</div><div class="v"><span class="up">${d.st_zt.length}</span><span class="muted" style="font-size:16px"> / </span><span class="down">${d.st_dt.length}</span></div><div class="s">ST 共 ${d.st_total} 只</div></div>`;
    table("lb-table", [
      { k: "name", t: "股票", f: (v, r) => stockLink(v, r) + (r.is_st ? ' <span class="pill warn">ST</span>' : "") },
      { k: "streak", t: "连板", n: 1, f: (v, r) => `<b class="up">${v}</b>${r.one_word ? '<span class="hint"> 一字</span>' : ""}` },
      { k: "amount", t: "成交额", n: 1, f: (v) => money(v) }, { k: "float_mv", t: "流通市值", n: 1, f: (v) => money(v, 0) },
      { k: "boards", t: "主线板块", f: (v) => esc(v || "—") },
      { k: "net_buy", t: "龙虎榜净买", n: 1, f: (v) => (isNum(v) ? `<b class="${cls(v)}">${money(v)}</b>` : '<span class="muted">未上榜</span>') },
      { k: "jg_net", t: "机构净买", n: 1, f: (v, r) => (isNum(v) ? `<span class="${cls(v)}">${money(v)}</span> <span class="hint">${r.jg_buyers}买${r.jg_sellers}卖</span>` : "—") },
      { k: "zt_reason", t: "涨停原因（同花顺）", f: (v, r) => (v ? `<div style="white-space:normal;min-width:180px">${tagChips(v.split("+"))}</div>` : '<span class="muted">—</span>') },
      { k: "explain", t: "解读", f: (v) => esc(v || "—") },
      { k: "reason", t: "上榜原因", f: (v) => `<span title="${esc(v || "")}">${esc((v || "—").slice(0, 18))}${v && v.length > 18 ? "…" : ""}</span>` },
      { k: "next_pct", t: "次日涨幅", n: 1, f: (v) => (isNum(v) ? `<span class="${cls(v)}">${pct(v)}</span>` : '<span class="muted">—</span>') },
    ], rows, { onClick: (r) => (r.reason ? openSeats(r.code, d.date, r.name) : null), emptyText: `当天没有 ≥${d.min} 连板的股票` });
    const h = d.height;
    if (h.series.length) {
      chart("ch-lb-height").setOption(base({
        grid: { left: 36, right: 16, top: 56, bottom: 28 },
        legend: { top: 0, type: "scroll", textStyle: { color: t.text2, fontSize: 12 } },
        xAxis: axis("category", { data: h.dates }), yAxis: axis("value", { minInterval: 1, name: "连板", nameTextStyle: { color: t.text3 } }),
        series: h.series.map((s) => ({ name: s.name, type: "line", data: s.data, symbolSize: 5, lineStyle: { width: 1.8 } })),
      }), true);
    } else emptyChart("ch-lb-height", "无连板股");
    $("#lb-st-note").textContent = d.lim_note;
    const stCols = [{ k: "name", t: "股票", f: stockLink }, { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
      { k: "streak", t: "连板", n: 1, f: (v) => (v ? v + "板" : "—") }, { k: "amount", t: "成交额", n: 1, f: (v) => money(v) }];
    table("lb-st-zt", stCols, d.st_zt, { emptyText: "当天没有 ST 涨停" });
    table("lb-st-dt", stCols, d.st_dt, { emptyText: "当天没有 ST 跌停" });
  };

  // ------------------------------------------------------------ 题材热榜（同花顺）
  let thDate = null, thHotKind = "stock", thHotDate = "";
  const tagChips = (tags, ml) => (tags || []).map((t) => `<span class="pill ${ml && ml.includes(t) ? "up" : "acc"}" style="margin:1px 2px">${esc(t)}</span>`).join("");
  loaders.themes = async () => {
    let d;
    try { d = await api("/api/themes" + (thDate ? `?date=${thDate}` : "")); }
    catch (e) { $("#th-kpis").innerHTML = `<div class="kpi">${esc(e.message)}</div>`; return; }
    if (!d.dates.length) { $("#th-kpis").innerHTML = `<div class="kpi">${esc(d.error || "没有数据")}</div>`; return; }
    const D = d.date.replace(/-/g, "");
    thDate = D;
    const sel = $("#th-date");
    sel.innerHTML = d.dates.slice().reverse().map((x) => `<option value="${x}">${fmtDate(x)}</option>`).join("");
    sel.value = D;
    sel.onchange = () => { thDate = sel.value; loaders.themes(); };
    const i = d.dates.indexOf(D);
    $("#th-prev").disabled = i <= 0; $("#th-next").disabled = i >= d.dates.length - 1;
    $("#th-prev").onclick = () => { thDate = d.dates[i - 1]; loaders.themes(); };
    $("#th-next").onclick = () => { thDate = d.dates[i + 1]; loaders.themes(); };

    const st = d.stats || {}, zt = (st.limit_up_count || {}).today || {}, zy = (st.limit_up_count || {}).yesterday || {}, dt = (st.limit_down_count || {}).today || {};
    const top = (d.ladder || [])[0];
    $("#th-kpis").innerHTML = `
      <div class="kpi" data-info="th_kpi"><div class="k">涨停</div><div class="v up">${zt.num ?? d.pool.length}</div><div class="s">昨日 ${zy.num ?? "—"}</div></div>
      <div class="kpi" data-info="th_kpi"><div class="k">封板率</div><div class="v">${pct01(zt.rate, 0)}</div><div class="s">触板 ${zt.history_num ?? "—"} · 炸板 ${zt.open_num ?? "—"}</div></div>
      <div class="kpi" data-info="th_kpi"><div class="k">跌停</div><div class="v down">${dt.num ?? "—"}</div><div class="s">昨日 ${((st.limit_down_count || {}).yesterday || {}).num ?? "—"}</div></div>
      <div class="kpi" data-info="th_kpi"><div class="k">最高板</div><div class="v">${top ? top.height : "—"}</div><div class="s">${top ? top.stocks.map((x) => esc(x.name)).join("、") : ""}</div></div>
      <div class="kpi" data-info="th_kpi"><div class="k">主线题材</div><div class="v" style="font-size:17px;line-height:1.5">${d.mainline.length ? d.mainline.map(esc).join(" · ") : "无"}</div></div>`;

    $("#th-rank-hint").textContent = `近 ${d.params.window} 日涨停家数排序；主线 = 前 ${d.params.top_k} 且 ≥${d.params.min_count} 家且今日仍有涨停`;
    table("th-rank", [
      { k: "tag", t: "题材", f: (v, r) => `<b>${esc(v)}</b>${r.is_main ? ' <span class="pill up">主线</span>' : ""}${r.generic ? ' <span class="pill" title="通用原因，不参与主线排名">通用</span>' : ""}` },
      { k: "strength", t: `${d.params.window}日`, n: 1 }, { k: "today", t: "今日", n: 1, f: (v) => `<b class="up">${v}</b>` },
      { k: "leader", t: "最高板", f: (v) => esc(v || "—") },
      { k: "stocks", t: "今日涨停", f: (v) => v.map((x) => `<span class="tag" data-stock="${x.code}">${esc(x.name)}${x.boards > 1 ? ` ${x.boards}板` : ""}</span>`).join(" ") || "—" },
    ], d.rank, { emptyText: "当天没有涨停原因数据" });

    const t = theme();
    if (d.trend && d.trend.series.length) {
      chart("ch-th-trend").setOption(base({
        grid: { left: 36, right: 16, top: 56, bottom: 28 },
        legend: { top: 0, type: "scroll", textStyle: { color: t.text2, fontSize: 12 } },
        tooltip: { ...base().tooltip, axisPointer: { type: "shadow" } },
        xAxis: axis("category", { data: d.trend.dates }), yAxis: axis("value", { minInterval: 1, name: "涨停家数", nameTextStyle: { color: t.text3 } }),
        series: d.trend.series.map((x) => ({ name: x.name, type: "bar", stack: "s", data: x.data, barMaxWidth: 16 })),
      }), true);
    } else emptyChart("ch-th-trend", "暂无");

    $("#th-ladder").innerHTML = (d.ladder || []).map((g) => `<div class="lad-row"><div class="lad-n">${g.height === 1 ? "首板" : g.height + "板"}<div style="font-size:11px;font-weight:500">${g.stocks.length}只</div></div>
      <div class="lad-s">${g.stocks.map((x) => `<span class="tag" data-stock="${x.code}">${esc(x.name)}</span>`).join("")}</div></div>`).join("") || `<div class="empty">当天无连板</div>`;

    const cols = [
      { k: "name", t: "股票", f: stockLink }, { k: "high_days", t: "几天几板", f: (v, r) => `<b class="${r.boards > 1 ? "up" : ""}">${esc(v || "—")}</b>` },
      { k: "tags", t: "涨停原因", f: (v) => `<div style="white-space:normal;min-width:220px">${tagChips(v, d.mainline)}</div>` },
      { k: "type", t: "类型" }, { k: "first_time", t: "首封", f: (v) => esc((v || "—").slice(0, 5)) }, { k: "last_time", t: "末封", f: (v) => esc((v || "—").slice(0, 5)) },
      { k: "open_num", t: "开板", n: 1 }, { k: "order_amount", t: "封单", n: 1, f: (v) => money(v) },
      { k: "suc_rate", t: "封板成功率", n: 1, f: (v) => pct01(v, 0) }, { k: "turnover", t: "换手", n: 1, f: (v) => (isNum(v) ? v.toFixed(1) + "%" : "—") },
      { k: "float_mv", t: "流通市值", n: 1, f: (v) => money(v, 0) },
    ];
    const drawPool = () => {
      const q = $("#th-filter").value.trim();
      table("th-pool", cols, q ? d.pool.filter((r) => (r.name + r.reason).includes(q)) : d.pool, { emptyText: "无" });
    };
    $("#th-filter").oninput = drawPool;
    drawPool();
    loadHot();
  };

  bindSeg("th-hot-kind", (v) => { thHotKind = v; loadHot(); });
  async function loadHot() {
    let d;
    try { d = await api(`/api/ths_hot?kind=${thHotKind}` + (thHotDate ? `&date=${thHotDate}` : "")); }
    catch (e) { $("#th-hot").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    const sel = $("#th-hot-date");
    sel.innerHTML = `<option value="">实时</option>` + (d.dates || []).slice().reverse().map((x) => `<option value="${x}">${fmtDate(x)} 归档</option>`).join("");
    sel.value = thHotDate;
    sel.onchange = () => { thHotDate = sel.value; loadHot(); };
    $("#th-hot-src").textContent = d.source || "";
    const chg = (v) => (isNum(v) && v !== 0 ? `<span class="${v > 0 ? "up" : "down"}">${v > 0 ? "↑" : "↓"}${Math.abs(v)}</span>` : "—");
    if (thHotKind === "stock") {
      table("th-hot", [
        { k: "rank", t: "排名", n: 1 }, { k: "name", t: "股票", f: stockLink },
        { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
        { k: "heat", t: "热度", n: 1, f: (v) => money(v, 0) }, { k: "rank_chg", t: "排名变化", n: 1, f: chg },
        { k: "concepts", t: "概念标签" }, { k: "popularity", t: "上榜情况" },
      ], d.rows);
    } else {
      table("th-hot", [
        { k: "rank", t: "排名", n: 1 }, { k: "name", t: "板块", f: (v) => `<b>${esc(v)}</b>` },
        { k: "pct", t: "涨跌幅", n: 1, f: (v) => `<span class="${cls(v)}">${pct(v)}</span>` },
        { k: "heat", t: "热度", n: 1, f: (v) => money(v, 0) }, { k: "rank_chg", t: "排名变化", n: 1, f: chg },
        { k: "tag", t: "标签" }, { k: "hot_tag", t: "上榜情况" },
        { k: "etf", t: "对应ETF", f: (v, r) => (v ? `${esc(v)} <span class="${cls(r.etf_pct)}">${pct(r.etf_pct)}</span>` : "—") },
      ], d.rows);
    }
  }

  // ------------------------------------------------------------ 策略中心
  let stKey = null, stItems = [], stData = null, stCat = store.get("stCat", "all");
  $$("#st-cat button").forEach((b) => b.classList.toggle("on", b.dataset.v === stCat));
  bindSeg("st-cat", (v) => { stCat = v; store.set("stCat", v); renderStrategies(); });
  const inCat = (x) => stCat === "all" || x.mode === stCat || x.key === "random_baseline";  // 随机基准两类都显示，作参照
  loaders.strategies = async () => {
    try { stData = await api("/api/strategies"); } catch (e) { $("#st-table").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    stItems = stData.items;
    renderStrategies();
  };

  function renderStrategies() {
    const d = stData;
    if (!d) return;
    const rows = d.items.filter(inCat).map((x) => {
      const r = x.result || {}, m = r.metrics || {}, i = r.in_sample || {}, o = r.out_sample || {};
      return { ...x, total: parseFloat(m["总收益率"]), mdd: parseFloat(m["最大回撤"]), trades: m["交易次数"], win: parseFloat(m["胜率"]),
        sharpe: m["夏普比率"], n_signals: r.n_signals, in_ret: i.ret, out_ret: o.ret, zero: r.zero_cost ? parseFloat(r.zero_cost.total) : null,
        updated: r.updated, active: x.key === d.active };
    });
    const pctCell = (v) => (isNum(v) ? `<span class="${cls(v)}">${pct(v, 1)}</span>` : "—");
    table("st-table", [
      { k: "name", t: "策略", f: (v, r) => `<a class="link" data-skey="${r.key}">${esc(v)}</a>${r.active ? ' <span class="pill acc">每日计划使用中</span>' : ""}${r.mode === "rebalance" ? ` <span class="pill acc">${r.universe === "etf" ? "ETF" : "个股"}·定期调仓</span>` : ""}${r.builtin ? "" : ' <span class="pill warn">自定义</span>'}<div class="hint wrap">${esc(r.description || "")}</div>` },
      { k: "n_signals", t: "信号", n: 1 }, { k: "trades", t: "交易", n: 1 },
      { k: "total", t: "总收益", n: 1, f: pctCell }, { k: "zero", t: "零成本收益", n: 1, f: pctCell },
      { k: "in_ret", t: "样本内", n: 1, f: (v) => pctCell(isNum(v) ? v * 100 : v) }, { k: "out_ret", t: "样本外", n: 1, f: (v) => pctCell(isNum(v) ? v * 100 : v) },
      { k: "mdd", t: "最大回撤", n: 1, f: (v) => (isNum(v) ? `<span class="down">${pct(v, 1, false)}</span>` : "—") },
      { k: "win", t: "胜率", n: 1, f: (v) => (isNum(v) ? v.toFixed(0) + "%" : "—") }, { k: "sharpe", t: "夏普", n: 1 },
      { k: "pinned", t: "总览显示", f: (v, r) => `<label class="hint" title="${r.active ? "每日策略总是显示" : "在总览「策略候选」中可切换查看"}"><input type="checkbox" data-pin="${r.key}" ${v || r.active ? "checked" : ""} ${r.active ? "disabled" : ""}></label>` },
      { k: "key", t: "操作", f: (v, r) => r.error ? `<span class="down" title="${esc(r.error)}">加载失败</span>` :
        `<button class="btn sm" data-run="${v}">回测</button> <button class="btn sm" data-src="${v}">代码</button>` + (r.active ? "" : ` <button class="btn sm" data-act="${v}">设为每日策略</button>`) },
    ], rows, { sort: { k: "total", asc: false } });
    $$("#st-table [data-skey]").forEach((a) => a.onclick = () => showStrategy(a.dataset.skey));
    $$("#st-table [data-run]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); startJob("compare", `only=${b.dataset.run}`); });
    $$("#st-table [data-src]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); showSource(b.dataset.src); });
    $$("#st-table [data-act]").forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      try { await api("/api/strategies/active", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: b.dataset.act }) }); }
      catch (er) { showToast(`<div class="t">设置失败</div><div class="l">${esc(er.message)}</div>`, 4000); return; }
      showToast(`<div class="t">已切换每日策略</div><div class="l">总览候选与「生成计划」将使用新策略</div>`, 4000);
      ovData = null; loaders.strategies();
    });
    $$("#st-table [data-pin]").forEach((cb) => {
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = async () => {
        try { await api("/api/strategies/pin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: cb.dataset.pin, on: cb.checked }) }); }
        catch (er) { showToast(`<div class="t">设置失败</div><div class="l">${esc(er.message)}</div>`, 4000); cb.checked = !cb.checked; return; }
        const it = stItems.find((x) => x.key === cb.dataset.pin);
        if (it) it.pinned = cb.checked;
        ovData = null;
        showToast(`<div class="t">${cb.checked ? "已加入" : "已移出"}总览</div><div class="l">总览「策略候选」卡片可切换查看</div>`, 3000);
      };
    });
    drawNav(new Set(rows.map((r) => r.key)));
    const first = (rows.find((r) => r.key === stKey) || rows.find((r) => r.active) || rows[0] || {}).key;
    if (first) showStrategy(first);
  }

  async function drawNav(keys) {
    const t = theme();
    let eqs;
    try { eqs = await api("/api/strategy_equities"); } catch { return; }
    eqs = eqs.filter((e) => e.key === "__index__" || !keys || keys.has(e.key));
    if (eqs.length <= 1) return emptyChart("ch-st-nav", "该分类还没有回测结果，点「全部回测」");
    const nameOf = (k) => (stItems.find((x) => x.key === k) || {}).name || k;
    chart("ch-st-nav").setOption(base({
      grid: { left: 50, right: 20, top: 60, bottom: 50 },
      legend: { top: 0, type: "scroll", textStyle: { color: t.text2, fontSize: 12 } },
      tooltip: { ...base().tooltip, order: "valueDesc", valueFormatter: (v) => (isNum(v) ? v.toFixed(3) : "—") },
      xAxis: axis("category", { data: eqs[0].dates }),
      yAxis: axis("value", { scale: true, name: "净值", nameTextStyle: { color: t.text3 } }),
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8, borderColor: t.line }],
      series: eqs.map((e) => e.key === "__index__"
        ? { name: e.name, type: "line", data: e.nav, showSymbol: false, lineStyle: { width: 1.5, type: "dashed", color: t.text3 }, itemStyle: { color: t.text3 } }
        : { name: nameOf(e.key), type: "line", data: e.nav, showSymbol: false, lineStyle: { width: e.key === "random_baseline" ? 1 : 1.6, type: e.key === "random_baseline" ? "dotted" : "solid" } }),
    }), true);
  }

  async function showStrategy(key) {
    stKey = key;
    const t = theme();
    const info = stItems.find((x) => x.key === key) || {};
    $("#st-d-title").textContent = `${info.name || key} · 资金曲线与回撤`;
    window.INFO_DYNAMIC.st_eq = `<b>${esc(info.name || key)}</b><br>${esc(info.description || "")}`
      + (info.mode === "rebalance" ? `<br>类型：定期调仓（${info.universe === "etf" ? "ETF池" : "个股"}）` : `<br>类型：短线信号；执行参数 ${esc(JSON.stringify(info.exec || {}))}`);
    let d;
    try { d = await api(`/api/strategy/${key}`); }
    catch (e) { emptyChart("ch-st-eq", e.message); emptyChart("ch-st-year", ""); $("#st-trades").innerHTML = ""; return; }
    chart("ch-st-eq").setOption(base({
      grid: [{ left: 60, right: 20, top: 36, height: "55%" }, { left: 60, right: 20, top: "74%", height: "16%" }],
      axisPointer: { link: [{ xAxisIndex: "all" }] }, legend: { show: false },
      xAxis: [axis("category", { data: d.dates }), axis("category", { data: d.dates, gridIndex: 1, axisLabel: { show: false } })],
      yAxis: [axis("value", { scale: true }), axis("value", { gridIndex: 1, axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%", color: t.text3, fontSize: 11 } })],
      tooltip: { ...base().tooltip, formatter: (ps) => `<b>${ps[0].axisValue}</b><br>` + ps.map((p) => `${p.marker}${p.seriesName} ${p.seriesName === "回撤" ? pct01(p.value, 1) : p.value.toFixed(0)}`).join("<br>") },
      series: [
        { name: "资金", type: "line", data: d.equity, showSymbol: false, lineStyle: { width: 1.8, color: t.accent }, itemStyle: { color: t.accent },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: t.accent + "33" }, { offset: 1, color: t.accent + "00" }]) } },
        { name: "回撤", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: d.drawdown, showSymbol: false, lineStyle: { width: 1, color: t.down }, itemStyle: { color: t.down }, areaStyle: { color: t.down + "33" } },
      ],
    }), true);
    const y = (info.result && info.result.yearly) || {};
    const ys = Object.keys(y);
    chart("ch-st-year").setOption(base({ legend: { show: false }, grid: { left: 50, right: 16, top: 24, bottom: 28 },
      tooltip: { trigger: "axis", valueFormatter: (v) => pct01(v, 1, true), backgroundColor: t.panel, borderColor: t.line, textStyle: { color: t.text } },
      xAxis: axis("category", { data: ys }), yAxis: axis("value", { axisLabel: { formatter: (v) => (v * 100).toFixed(0) + "%", color: t.text3, fontSize: 11 } }),
      series: [{ type: "bar", name: "收益", data: ys.map((k) => ({ value: y[k], itemStyle: { color: y[k] >= 0 ? t.up : t.down, borderRadius: 4 } })), barMaxWidth: 36,
        label: { show: true, position: "top", formatter: (p) => pct01(p.value, 1, true), color: t.text2, fontSize: 11 } }] }), true);
    table("st-trades", [
      { k: "name", t: "股票", f: (v, r) => stockLink(v || r.code, r) }, { k: "buy_date", t: "买入日" }, { k: "sell_date", t: "卖出日" },
      { k: "buy_px", t: "买价", n: 1, f: (v) => v.toFixed(2) }, { k: "shares", t: "股数", n: 1 },
      { k: "ret", t: "收益率", n: 1, f: (v) => `<span class="${cls(v)}">${pct01(v, 2, true)}</span>` },
      { k: "pnl", t: "盈亏", n: 1, f: (v) => `<span class="${cls(v)}">${v.toFixed(0)}</span>` },
      { k: "hold", t: "持有天", n: 1 }, { k: "reason", t: "卖出原因" },
    ], d.trades, { emptyText: "没有成交" });
  }

  async function showSource(key) {
    openDrawer(`${key}.py`, `<div class="loading">加载…</div>`);
    try {
      const d = await api(`/api/strategy_source/${key}`);
      $("#drawer-body").innerHTML = `<div class="hint" style="margin-bottom:8px">文件位置：strategies/${esc(key)}.py（用任意编辑器修改，保存后点「回测」即可）</div><pre class="log" style="height:auto;max-height:calc(100vh - 140px)">${esc(d.source)}</pre>`;
    } catch (e) { $("#drawer-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  $("#st-upload").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api(`/api/strategies/upload?overwrite=${$("#st-overwrite").checked ? 1 : 0}`, { method: "POST", body: fd });
      showToast(`<div class="t">已导入 ${esc(r.key)}.py</div><div class="l">正在回测这个策略…</div>`, 4000);
      startJob("compare", `only=${r.key}`);
    } catch (er) { showToast(`<div class="t">导入失败</div><div class="l">${esc(er.message)}</div>`, 6000); }
    e.target.value = "";
  };

  // ------------------------------------------------------------ 数据源
  $("#src-check").onclick = async () => {
    $("#src-table").innerHTML = `<div class="loading">检测中…</div>`;
    try {
      const d = await api("/api/sources");
      table("src-table", [
        { k: "name", t: "数据源" }, { k: "use", t: "用途" },
        { k: "ok", t: "状态", f: (v) => (v ? '<span class="pill down">可用</span>' : '<span class="pill up">不可用</span>') },
        { k: "ms", t: "耗时", n: 1, f: (v) => v + "ms" }, { k: "msg", t: "详情" },
      ], d);
    } catch (e) { $("#src-table").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  // ------------------------------------------------------------ 任务（按钮）
  let polling = null;
  const toast = $("#toast");
  function showToast(html, ms) {
    toast.innerHTML = html; toast.classList.add("show");
    clearTimeout(showToast.t);
    if (ms) showToast.t = setTimeout(() => toast.classList.remove("show"), ms);
  }
  async function startJob(name, query = "") {
    try {
      await api(`/api/jobs/${name}${query ? "?" + query : ""}`, { method: "POST" });
    } catch (e) { showToast(`<div class="t">无法启动</div><div class="l">${esc(e.message)}</div>`, 4000); return; }
    pollJob();
  }
  $$("[data-job]").forEach((b) => b.onclick = () => startJob(b.dataset.job));
  function renderJob(j) {
    const badge = $("#job-state");
    badge.className = "badge " + (j.state || "");
    badge.textContent = { idle: "空闲", running: `运行中 · ${j.title}`, done: `完成 · ${j.title}`, failed: `失败 · ${j.title}` }[j.state] || j.state;
    if (j.log && j.log.length) {
      const log = $("#job-log");
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent = j.log.join("\n");
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
    $$("[data-job]").forEach((b) => b.disabled = j.state === "running");
    $$('[data-job="sync"]').forEach((b) => b.classList.toggle("busy", j.state === "running"));
  }
  async function pollJob() {
    clearTimeout(polling);
    let j;
    try { j = await api("/api/jobs"); } catch { polling = setTimeout(pollJob, 3000); return; }
    renderJob(j);
    if (j.state === "running") {
      const last = (j.log || []).slice(-1)[0] || "启动中…";
      showToast(`<div class="t">${esc(j.title)} · 运行中（开始于 ${j.started}）</div><div class="l">${esc(last)}</div><div class="bar"><i></i></div>`);
      polling = setTimeout(pollJob, 1500);
    } else if (pollJob.wasRunning) {
      showToast(`<div class="t">${j.state === "done" ? "✓" : "✕"} ${esc(j.title)} ${j.state === "done" ? "完成" : "失败"}</div><div class="l">${j.state === "done" ? "页面数据已刷新" : "详情见「任务日志」"}</div>`, 6000);
      ovData = null;
      loadStatus();
      route();
    }
    pollJob.wasRunning = j.state === "running";
  }

  // ------------------------------------------------------------ 盘中提示
  let wtDay = null, wtFilter = "pick", wtTimer = null, wtFirst = true;
  const wtSeen = new Set();
  bindSeg("wt-filter", (v) => { wtFilter = v; loaders.watch(); });
  function wtAlert(title, body) {
    if (window.Notification && Notification.permission === "granted") new Notification(title, { body });
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination); o.frequency.value = 880; g.gain.value = 0.08;
      o.start(); o.stop(ctx.currentTime + 0.25);
    } catch { /* 浏览器没有声音权限 */ }
  }
  function wtNotify(d) {
    const fresh = [];
    d.events.filter((e) => e.pick).forEach((e) => fresh.push([`e${d.day}${e.code}`, `候选封板 ${e.name}`, `${e.time} 第${e.boards}板，封单 ${e.seal_pct}%`]));
    d.prompts.filter((r) => r.state === "sell").forEach((r) => fresh.push([`s${r.kind}${r.code}${r.buy_day}`, `卖出提示 ${r.name}`, r.text]));
    fresh.forEach(([id, t, b]) => {
      if (!wtSeen.has(id)) { wtSeen.add(id); if (!wtFirst) wtAlert(t, b); }
    });
    wtFirst = false;
  }
  loaders.watch = async () => {
    clearTimeout(wtTimer);
    let d;
    try { d = await api("/api/watch" + (wtDay ? `?day=${wtDay}` : "")); } catch (e) { $("#wt-status").textContent = e.message; return; }
    const s = d.status;
    const btn = $("#wt-toggle");
    btn.textContent = s.running ? "停止监控" : "启动监控";
    btn.classList.toggle("primary", !s.running);
    btn.onclick = async () => {
      try { await api(`/api/watch/${s.running ? "stop" : "start"}`, { method: "POST" }); } catch (e) { showToast(esc(e.message), 3000); }
      setTimeout(loaders.watch, 1500);
    };
    const nb = $("#wt-notify");
    const perm = window.Notification ? Notification.permission : "denied";
    nb.textContent = perm === "granted" ? "桌面提醒已开启" : "开启桌面提醒";
    nb.disabled = perm === "granted";
    nb.onclick = async () => { if (window.Notification) await Notification.requestPermission(); loaders.watch(); };
    $("#wt-status").textContent = s.running
      ? `${s.message}：监控 ${s.n_codes} 只${s.last ? `，最近更新 ${s.last}，每轮用时 ${s.cycle} 秒` : ""}`
      : `${s.message || "未启动"}。交易日 09:25 前启动，15:01 自动结束。`;
    const seals = d.events.filter((e) => e.event === "封板");
    const breaks = d.events.filter((e) => e.event === "炸板");
    const kpi = (k, v) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    $("#wt-kpis").innerHTML = kpi("当日封板", seals.length) + kpi("炸板次数", breaks.length)
      + kpi("候选", seals.filter((e) => e.pick).length) + kpi("纸面持仓", seals.filter((e) => e.book).length)
      + kpi("已记录交易日", d.stats.days);
    table("wt-prompts", [
      { k: "kind", t: "类型" }, { k: "name", t: "股票", f: stockLink },
      { k: "buy_day", t: "买入日", f: fmtDate }, { k: "buy_px", t: "买入价", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") },
      { k: "price", t: "现价", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") },
      { k: "text", t: "提示", f: (v, r) => `<span class="wt-state ${r.state}">${esc(v)}</span>` },
      { k: "code", t: "", f: (v, r) => (r.kind === "真实" ? `<a class="tag" data-wt-del="${v}">已卖出</a>` : "") },
    ], d.prompts, { emptyText: "现在没有持仓。候选封板后，这里会给出次日的卖出提示" });
    $("#wt-day").innerHTML = (d.days.length ? d.days : [d.day]).slice().reverse().map((x) => `<option value="${x}">${fmtDate(x)}</option>`).join("");
    $("#wt-day").value = d.day;
    $("#wt-day").onchange = (e) => { wtDay = e.target.value; loaders.watch(); };
    const rows = d.events.filter((e) => (wtFilter === "pick" ? e.pick : wtFilter === "seal" ? e.event === "封板" : true)).slice().reverse();
    table("wt-events", [
      { k: "time", t: "时间" }, { k: "name", t: "股票", f: stockLink }, { k: "event", t: "事件" },
      { k: "boards", t: "板数", n: 1 }, { k: "price", t: "价格", n: 1, f: (v) => (isNum(v) ? v.toFixed(2) : "—") },
      { k: "seal_amt", t: "封单额", n: 1, f: (v) => money(v) }, { k: "seal_pct", t: "封单比例", n: 1, f: (v) => pct(v, 2, false) },
      { k: "float_mv", t: "流通市值", n: 1, f: (v) => money(v) },
      { k: "pick", t: "标记", f: (v, r) => (r.one_word ? "一字" : r.book ? "纸面" : v ? "候选" : "") },
      { k: "reason", t: "涨停原因", f: (v) => esc(v || "—") },
    ], rows, { emptyText: wtFilter === "pick" ? "这一天还没有候选" : "这一天还没有事件" });
    $("#wt-verdict").textContent = d.stats.verdict;
    const p2 = (v) => `<span class="${cls(v)}">${pct(v)}</span>`;
    table("wt-stats", [
      { k: "name", t: "分组" }, { k: "n", t: "笔数", n: 1 }, { k: "sealed", t: "收盘封住率", n: 1, f: (v) => pct(v, 1, false) },
      { k: "r_all", t: "全部成交", n: 1, f: p2 }, { k: "r_fill", t: "只成交3成", n: 1, f: p2 },
      { k: "win", t: "胜率", n: 1, f: (v) => pct(v, 1, false) }, { k: "r_close", t: "次日收盘卖", n: 1, f: p2 },
    ], d.stats.rows, { emptyText: "还没有可结算的记录" });
    wtNotify(d);
    if (current === "watch") wtTimer = setTimeout(loaders.watch, 5000);
  };
  $("#wt-add").onclick = async () => {
    const code = (window.prompt("股票代码（6 位）") || "").trim();
    if (!code) return;
    const px = (window.prompt("买入价") || "").trim();
    if (!px) return;
    try {
      await api("/api/watch/holding", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, buy_px: px }) });
    } catch (e) { showToast(esc(e.message), 3000); }
    loaders.watch();
  };
  document.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-wt-del]");
    if (!a) return;
    await api(`/api/watch/holding/${a.dataset.wtDel}`, { method: "DELETE" });
    loaders.watch();
  });

  // ------------------------------------------------------------ 主题切换
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    store.set("theme", t);
  }
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(store.get("theme", prefersDark ? "dark" : "light"));
  $("#theme-btn").onclick = () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    Object.keys(charts).forEach((k) => { charts[k].dispose(); delete charts[k]; });
    if (ovData && current === "overview") { renderOverview(ovData); loadIndex(); loadMarketFlow(); } else route();
  };

  // ------------------------------------------------------------ 启动
  loadStatus();
  route();
  pollJob();
})();
