(function () {
  if (!window.echarts || window.echarts.__quantNumberFormattingInstalled) return;

  const asNumber = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  };
  const formatNumber = (value) => {
    const numeric = asNumber(value);
    if (numeric === null) return "-";
    const absolute = Math.abs(numeric);
    if (absolute >= 100_000_000) return `${(numeric / 100_000_000).toFixed(2)}億`;
    if (absolute >= 10_000) return `${(numeric / 10_000).toFixed(1)}萬`;
    if (absolute >= 1) return numeric.toFixed(2);
    if (absolute >= 0.01) return numeric.toFixed(4);
    return numeric.toFixed(6);
  };
  const formatTooltipValue = (value) => {
    if (Array.isArray(value)) return value.map(formatNumber).join("／");
    return formatNumber(value);
  };
  const installAxisFormatter = (axis) => {
    if (!axis || typeof axis !== "object" || axis.type === "category") return;
    axis.axisLabel = axis.axisLabel || {};
    if (!axis.axisLabel.formatter) axis.axisLabel.formatter = formatNumber;
  };
  const installTooltipStyle = (tooltip) => {
    if (!tooltip || typeof tooltip !== "object") return;
    tooltip.confine = true;
    tooltip.backgroundColor = "rgba(7, 17, 30, 0.30)";
    tooltip.borderColor = "rgba(103, 213, 255, 0.42)";
    tooltip.borderWidth = 1;
    tooltip.padding = [9, 11];
    tooltip.textStyle = {
      ...(tooltip.textStyle || {}),
      color: "#e8f0fa",
      fontSize: 12,
      lineHeight: 19,
    };
    tooltip.extraCssText = "backdrop-filter:blur(8px);box-shadow:0 8px 24px rgba(0,0,0,.22);border-radius:6px;";
  };
  const installFormatters = (option) => {
    if (!option || typeof option !== "object") return option;
    const tooltip = option.tooltip;
    if (tooltip && typeof tooltip === "object") {
      installTooltipStyle(tooltip);
      if (!tooltip.formatter && !tooltip.valueFormatter) {
        tooltip.valueFormatter = formatTooltipValue;
      }
    }
    const axes = option.yAxis;
    (Array.isArray(axes) ? axes : [axes]).forEach(installAxisFormatter);
    return option;
  };

  const originalInit = window.echarts.init.bind(window.echarts);
  window.echarts.init = function (...args) {
    const chart = originalInit(...args);
    const originalSetOption = chart.setOption.bind(chart);
    chart.setOption = function (option, ...rest) {
      return originalSetOption(installFormatters(option), ...rest);
    };
    return chart;
  };
  window.echarts.__quantNumberFormattingInstalled = true;
  window.quantChartNumber = formatNumber;
})();
