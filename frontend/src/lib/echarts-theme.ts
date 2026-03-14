/** Nodeglow ECharts themes — dark & light */

const shared = {
  backgroundColor: 'transparent',
  textStyle: { fontFamily: 'Inter, sans-serif', fontSize: 12 },
  color: [
    '#38BDF8', '#A78BFA', '#34D399', '#FBBF24', '#F87171',
    '#60A5FA', '#FB923C', '#E879F9', '#2DD4BF', '#818CF8',
  ],
  grid: { left: 8, right: 8, top: 32, bottom: 8, containLabel: true },
  line: { smooth: true, symbolSize: 4, lineStyle: { width: 2 }, areaStyle: { opacity: 0.08 } },
  bar: { barMaxWidth: 24, itemStyle: { borderRadius: [4, 4, 0, 0] } },
};

export const nodeglowDark = {
  ...shared,
  textStyle: { ...shared.textStyle, color: '#94A3B8' },
  title: { textStyle: { color: '#F1F5F9', fontSize: 14, fontWeight: 600 } },
  legend: { textStyle: { color: '#94A3B8' } },
  categoryAxis: {
    axisLine: { lineStyle: { color: '#1E2433' } },
    axisTick: { show: false },
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E2433', type: 'dashed' as const } },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#64748B' },
    splitLine: { lineStyle: { color: '#1E2433', type: 'dashed' as const } },
  },
  tooltip: {
    backgroundColor: '#1A1F2E',
    borderColor: '#2A3144',
    textStyle: { color: '#F1F5F9', fontSize: 12 },
    extraCssText: 'backdrop-filter: blur(12px); border-radius: 8px;',
  },
};

export const nodeglowLight = {
  ...shared,
  textStyle: { ...shared.textStyle, color: '#475569' },
  title: { textStyle: { color: '#0F172A', fontSize: 14, fontWeight: 600 } },
  legend: { textStyle: { color: '#475569' } },
  categoryAxis: {
    axisLine: { lineStyle: { color: '#CBD5E1' } },
    axisTick: { show: false },
    axisLabel: { color: '#475569' },
    splitLine: { lineStyle: { color: '#E2E8F0', type: 'dashed' as const } },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#475569' },
    splitLine: { lineStyle: { color: '#E2E8F0', type: 'dashed' as const } },
  },
  tooltip: {
    backgroundColor: '#F4F7FA',
    borderColor: '#CBD5E1',
    textStyle: { color: '#0F172A', fontSize: 12 },
    extraCssText: 'backdrop-filter: blur(12px); border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);',
  },
};

/** Legacy export for backwards compat */
export const nodeglowTheme = nodeglowDark;
