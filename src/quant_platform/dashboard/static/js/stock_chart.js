(function () {
  const payload = window.stockChartPayload;
  const element = document.getElementById("stockCandleChart");
  if (!payload || !element || !window.echarts) return;

  const round = (value, digits) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Number(numeric.toFixed(digits)) : null;
  };
  const price = (value) => round(value, 2);
  const technical = (value) => round(value, 4);
  const priceText = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(2) : "-";
  };
  const technicalText = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(4) : "-";
  };
  const volumeText = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    if (Math.abs(numeric) >= 100_000_000) return `${(numeric / 100_000_000).toFixed(2)}億`;
    if (Math.abs(numeric) >= 10_000) return `${(numeric / 10_000).toFixed(1)}萬`;
    return Math.round(numeric).toLocaleString("zh-TW");
  };
  const candles = payload.candles.map((item) => item.map(price));
  const closes = candles.map((item) => item[1]);
  const movingAverage = (period) => closes.map((_, index) => {
    if (index + 1 < period) return null;
    const windowValues = closes.slice(index + 1 - period, index + 1);
    return price(windowValues.reduce((sum, value) => sum + value, 0) / period);
  });
  const bollinger = (period = 20, width = 2) => {
    const middle = movingAverage(period);
    const upper = [], lower = [];
    closes.forEach((_, index) => {
      if (index + 1 < period) {
        upper.push(null); lower.push(null); return;
      }
      const values = closes.slice(index + 1 - period, index + 1);
      const mean = middle[index];
      const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / period;
      const deviation = Math.sqrt(variance);
      upper.push(price(mean + width * deviation));
      lower.push(price(mean - width * deviation));
    });
    return { middle, upper, lower };
  };
  const ema = (period) => {
    const multiplier = 2 / (period + 1);
    const output = [];
    closes.forEach((value, index) => {
      output.push(technical(index === 0 ? value : value * multiplier + output[index - 1] * (1 - multiplier)));
    });
    return output;
  };
  const rsi = (period = 14) => {
    const output = Array(closes.length).fill(null);
    if (closes.length <= period) return output;
    let gains = 0, losses = 0;
    for (let index = 1; index <= period; index += 1) {
      const change = closes[index] - closes[index - 1];
      gains += Math.max(change, 0); losses += Math.max(-change, 0);
    }
    let averageGain = gains / period, averageLoss = losses / period;
    output[period] = technical(averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss));
    for (let index = period + 1; index < closes.length; index += 1) {
      const change = closes[index] - closes[index - 1];
      averageGain = (averageGain * (period - 1) + Math.max(change, 0)) / period;
      averageLoss = (averageLoss * (period - 1) + Math.max(-change, 0)) / period;
      output[index] = technical(averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss));
    }
    return output;
  };

  const ma5 = movingAverage(5), ma20 = movingAverage(20), ma60 = movingAverage(60);
  const bands = bollinger();
  const ema12 = ema(12), ema26 = ema(26);
  const macd = closes.map((_, index) => technical(ema12[index] - ema26[index]));
  const signalMultiplier = 2 / 10;
  const signal = [];
  macd.forEach((value, index) => {
    signal.push(
      index === 0
        ? value
        : technical(value * signalMultiplier + signal[index - 1] * (1 - signalMultiplier))
    );
  });
  const histogram = macd.map((value, index) => technical(value - signal[index]));
  const chart = echarts.init(element);
  const line = (name, data, color, xAxisIndex = 0, yAxisIndex = 0, extra = {}) => ({
    name, type: "line", data, xAxisIndex, yAxisIndex, showSymbol: false,
    smooth: false, connectNulls: true, lineStyle: { width: 1.3, color },
    itemStyle: { color }, ...extra,
  });

  chart.setOption({
    animation: false,
    legend: { type: "scroll", top: 0, textStyle: { color: "#91a2b8" } },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      confine: true,
      backgroundColor: "rgba(7, 17, 30, 0.30)",
      borderColor: "rgba(103, 213, 255, 0.42)",
      borderWidth: 1,
      padding: [9, 11],
      textStyle: { color: "#e8f0fa", fontSize: 12, lineHeight: 19 },
      extraCssText: "backdrop-filter:blur(8px);box-shadow:0 8px 24px rgba(0,0,0,.22);border-radius:6px;",
      formatter: (params) => {
        const date = params[0]?.axisValueLabel || "";
        const values = params.map((item) => {
          const value = item.value;
          const text = Array.isArray(value)
            ? `開 ${priceText(value[0])}／收 ${priceText(value[1])}／低 ${priceText(value[2])}／高 ${priceText(value[3])}`
            : item.seriesName === "成交量"
            ? volumeText(value)
            : ["RSI（14日）", "MACD", "訊號線", "MACD柱狀圖"].includes(item.seriesName)
            ? technicalText(value)
            : priceText(value);
          return `${item.marker}${item.seriesName}：${text}`;
        });
        return [date, ...values].join("<br/>");
      },
    },
    axisPointer: { link: [{ xAxisIndex: [0, 1, 2, 3] }] },
    grid: [
      { left: 58, right: 22, top: 42, height: "43%" },
      { left: 58, right: 22, top: "55%", height: "10%" },
      { left: 58, right: 22, top: "69%", height: "10%" },
      { left: 58, right: 22, top: "83%", height: "10%" },
    ],
    xAxis: [0, 1, 2, 3].map((gridIndex) => ({
      type: "category", gridIndex, data: payload.dates, boundaryGap: true,
      axisLabel: { show: gridIndex === 3, color: "#91a2b8" },
      axisLine: { lineStyle: { color: "#314158" } },
    })),
    yAxis: [
      { scale: true, axisLabel: { color: "#91a2b8", formatter: priceText }, splitLine: { lineStyle: { color: "#223046" } } },
      { gridIndex: 1, scale: true, name: "成交量", nameTextStyle: { color: "#91a2b8" }, axisLabel: { color: "#91a2b8", formatter: volumeText }, splitLine: { show: false } },
      { gridIndex: 2, min: 0, max: 100, name: "RSI", nameTextStyle: { color: "#91a2b8" }, axisLabel: { color: "#91a2b8", formatter: (value) => Number(value).toFixed(0) }, splitLine: { lineStyle: { color: "#223046" } } },
      { gridIndex: 3, scale: true, name: "MACD", nameTextStyle: { color: "#91a2b8" }, axisLabel: { color: "#91a2b8", formatter: technicalText }, splitLine: { lineStyle: { color: "#223046" } } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2, 3], start: 50, end: 100 },
      { type: "slider", xAxisIndex: [0, 1, 2, 3], start: 50, end: 100, bottom: 1, height: 15 },
    ],
    series: [
      { name: "K線", type: "candlestick", data: candles, itemStyle: { color: "#65e6a4", color0: "#ff6f7d", borderColor: "#65e6a4", borderColor0: "#ff6f7d" } },
      line("週均線（MA5）", ma5, "#f6c85f"),
      line("月均線（MA20）", ma20, "#5da5da"),
      line("季均線（MA60）", ma60, "#b276b2"),
      line("布林上軌", bands.upper, "#7ccba2", 0, 0, { lineStyle: { width: 1, color: "#7ccba2", type: "dashed" } }),
      line("布林中軌", bands.middle, "#4d9f83", 0, 0, { lineStyle: { width: 1, color: "#4d9f83" } }),
      line("布林下軌", bands.lower, "#7ccba2", 0, 0, { lineStyle: { width: 1, color: "#7ccba2", type: "dashed" } }),
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: payload.volumes, itemStyle: { color: "#47657a" } },
      line("RSI（14日）", rsi(), "#ff9da7", 2, 2, { markLine: { silent: true, symbol: "none", lineStyle: { type: "dashed", color: "#607589" }, data: [{ yAxis: 30 }, { yAxis: 70 }] } }),
      line("MACD", macd, "#5da5da", 3, 3),
      line("訊號線", signal, "#f6c85f", 3, 3),
      { name: "MACD柱狀圖", type: "bar", xAxisIndex: 3, yAxisIndex: 3, data: histogram, itemStyle: { color: (item) => item.value >= 0 ? "#65e6a4" : "#ff6f7d" } },
    ],
  });

  document.querySelectorAll("[data-stock-series]").forEach((control) => {
    control.addEventListener("change", () => {
      const names = control.dataset.stockSeries.split("|");
      names.forEach((name) => chart.dispatchAction({
        type: control.checked ? "legendSelect" : "legendUnSelect", name,
      }));
    });
  });
  addEventListener("resize", () => chart.resize());
})();
