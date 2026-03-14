'use client';

import { useRef, useEffect } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, GaugeChart as EGaugeChart } from 'echarts/charts';
import {
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { nodeglowDark, nodeglowLight } from '@/lib/echarts-theme';
import { useThemeStore } from '@/stores/theme';

echarts.use([
  BarChart, LineChart, PieChart, EGaugeChart,
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent, CanvasRenderer,
]);

echarts.registerTheme('nodeglow-dark', nodeglowDark);
echarts.registerTheme('nodeglow-light', nodeglowLight);

interface EChartProps {
  option: EChartsOption;
  className?: string;
  height?: number | string;
}

export function EChart({ option, className, height = 200 }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const colorMode = useThemeStore((s) => s.colorMode);

  // Init/reinit chart when theme changes
  useEffect(() => {
    if (!ref.current) return;
    chartRef.current?.dispose();
    const themeName = colorMode === 'light' ? 'nodeglow-light' : 'nodeglow-dark';
    chartRef.current = echarts.init(ref.current, themeName);
    chartRef.current.setOption(option, true);
    const obs = new ResizeObserver(() => chartRef.current?.resize());
    obs.observe(ref.current);
    return () => {
      obs.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [colorMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Update option when it changes
  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={className} style={{ height }} />;
}
