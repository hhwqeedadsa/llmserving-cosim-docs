(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";

  function el(name, attrs, text) {
    const node = document.createElementNS(NS, name);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmt(value) {
    return new Intl.NumberFormat("zh-CN").format(value);
  }

  function quantile(values, q) {
    if (!values.length) return 0;
    const sorted = values.slice().sort((a, b) => a - b);
    return sorted[Math.round((sorted.length - 1) * q)];
  }

  function scale(value, d0, d1, r0, r1) {
    if (d1 === d0) return (r0 + r1) / 2;
    return r0 + ((value - d0) / (d1 - d0)) * (r1 - r0);
  }

  function baseSvg(host, height) {
    const width = Math.max(360, Math.floor(host.getBoundingClientRect().width || 760));
    host.replaceChildren();
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
    host.appendChild(svg);
    return { svg, width, height, left: 64, right: 18, top: 18, bottom: 42 };
  }

  function axisText(svg, x, y, text, anchor) {
    svg.appendChild(el("text", { x, y, "text-anchor": anchor || "middle", class: "chart-label" }, text));
  }

  function drawPort(host, port) {
    const c = baseSvg(host, 280);
    const points = port.points;
    const maxT = Math.max(...points.map((p) => p.t), 1);
    const maxY = Math.max(...points.map((p) => p.bytes), 1);
    const x0 = c.left, x1 = c.width - c.right, y0 = c.height - c.bottom, y1 = c.top;

    for (let i = 0; i <= 4; i += 1) {
      const y = y0 - ((y0 - y1) * i) / 4;
      c.svg.appendChild(el("line", { x1: x0, y1: y, x2: x1, y2: y, class: "chart-grid" }));
      axisText(c.svg, x0 - 8, y + 4, fmt(Math.round((maxY * i) / 4)), "end");
    }
    for (let i = 0; i <= 4; i += 1) {
      const x = x0 + ((x1 - x0) * i) / 4;
      axisText(c.svg, x, y0 + 20, `${Math.round((maxT * i) / 4000)} µs`);
    }
    c.svg.appendChild(el("line", { x1: x0, y1: y0, x2: x1, y2: y0, class: "chart-axis" }));
    c.svg.appendChild(el("line", { x1: x0, y1: y0, x2: x0, y2: y1, class: "chart-axis" }));

    const path = points.map((p, i) => {
      const x = scale(p.t, 0, maxT, x0, x1);
      const y = scale(p.bytes, 0, maxY, y0, y1);
      return `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");
    c.svg.appendChild(el("path", { d: path, class: "port-line" }));
    axisText(c.svg, (x0 + x1) / 2, c.height - 7, "端口本地时间");
    const yTitle = el("text", {
      x: 14, y: (y0 + y1) / 2, transform: `rotate(-90 14 ${(y0 + y1) / 2})`,
      "text-anchor": "middle", class: "chart-label"
    }, "窗口线速字节 (B)");
    c.svg.appendChild(yTitle);
  }

  function drawHistogram(host, transfers) {
    const c = baseSvg(host, 280);
    const values = transfers.map((t) => t.txn);
    if (!values.length) {
      axisText(c.svg, c.width / 2, c.height / 2, "当前筛选没有传输");
      return;
    }
    const maxV = Math.max(...values, 1);
    const bins = 24;
    const counts = new Array(bins).fill(0);
    values.forEach((v) => {
      counts[Math.min(bins - 1, Math.floor((v / maxV) * bins))] += 1;
    });
    const maxCount = Math.max(...counts, 1);
    const x0 = c.left, x1 = c.width - c.right, y0 = c.height - c.bottom, y1 = c.top;
    const barW = (x1 - x0) / bins;
    counts.forEach((count, i) => {
      const h = scale(count, 0, maxCount, 0, y0 - y1);
      c.svg.appendChild(el("rect", {
        x: x0 + i * barW + 1, y: y0 - h,
        width: Math.max(1, barW - 2), height: h, class: "hist-bar"
      }));
    });
    c.svg.appendChild(el("line", { x1: x0, y1: y0, x2: x1, y2: y0, class: "chart-axis" }));
    c.svg.appendChild(el("line", { x1: x0, y1: y0, x2: x0, y2: y1, class: "chart-axis" }));
    for (let i = 0; i <= 4; i += 1) {
      const x = x0 + ((x1 - x0) * i) / 4;
      axisText(c.svg, x, y0 + 20, `${Math.round((maxV * i) / 4000)} µs`);
    }
    const p95 = quantile(values, 0.95);
    const px = scale(p95, 0, maxV, x0, x1);
    c.svg.appendChild(el("line", { x1: px, y1, x2: px, y2: y0, class: "p95-line" }));
    axisText(c.svg, Math.min(px + 4, x1 - 4), y1 + 12, `P95 ${fmt(p95)} ns`, px > x1 - 100 ? "end" : "start");
    axisText(c.svg, (x0 + x1) / 2, c.height - 7, "submit → transaction_done");
  }

  function renderTable(host, transfers) {
    const rows = transfers.slice().sort((a, b) => b.txn - a.txn).slice(0, 12);
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>transfer</th><th>src→dst</th><th>bytes</th><th>ready ns</th><th>ACK tail ns</th><th>transaction ns</th></tr></thead>";
    const body = document.createElement("tbody");
    rows.forEach((t) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td><code>${t.id}</code></td><td>${t.src}→${t.dst}</td><td>${fmt(t.bytes)}</td><td>${fmt(t.ready)}</td><td>${fmt(t.ackTail)}</td><td>${fmt(t.txn)}</td>`;
      body.appendChild(row);
    });
    table.appendChild(body);
    host.replaceChildren(table);
  }

  function init() {
    const root = document.getElementById("run-explorer");
    const data = window.MOE_SAMPLE_RUN;
    if (!root || !data) return;

    const summary = root.querySelector(".explorer-summary");
    const s = data.summary;
    const cards = [
      [fmt(s.requests), "完成请求"],
      [fmt(s.transfers), "Transfer 数"],
      [`${fmt(s.txnP95Ns)} ns`, "Transaction P95"],
      [`${fmt(s.txnMaxNs)} ns`, "Transaction 最大值"],
    ];
    cards.forEach(([value, label]) => {
      const card = document.createElement("div");
      card.innerHTML = `<b>${value}</b><span>${label}</span>`;
      summary.appendChild(card);
    });

    const portSelect = root.querySelector("#port-select");
    data.ports.forEach((port, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${port.id} · ${fmt(port.totalBytes)} B`;
      portSelect.appendChild(option);
    });
    const kindSelect = root.querySelector("#kind-select");
    const portHost = root.querySelector("#port-chart");
    const latencyHost = root.querySelector("#latency-chart");
    const tableHost = root.querySelector("#slow-table");

    function filtered() {
      const kind = kindSelect.value;
      return kind === "all" ? data.transfers : data.transfers.filter((t) => t.kind === kind);
    }

    function redraw() {
      drawPort(portHost, data.ports[Number(portSelect.value) || 0]);
      const current = filtered();
      drawHistogram(latencyHost, current);
      renderTable(tableHost, current);
    }

    portSelect.addEventListener("change", redraw);
    kindSelect.addEventListener("change", redraw);
    const observer = new ResizeObserver(redraw);
    observer.observe(root);
    redraw();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());

