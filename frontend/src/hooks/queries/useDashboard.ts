import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

export interface DashboardData {
  host_stats: HostStat[];
  online_count: number;
  offline_count: number;
  total_count: number;
  integration_health: IntHealth[];
  active_incidents: number;
  syslog_stats: SyslogStats;
  speedtest_data: SpeedtestData | null;
  storage_pools: StoragePool[];
  container_data: ContainerData | null;
  ups_data: UpsData | null;
  ssl_certs: SslCert[];
  recent_incidents: RecentIncident[];
  incident_trend: IncidentTrendDay[];
  heatmap_data: HeatmapHost[];
  heatmap_days: string[];
  layout: WidgetLayout[];
  top_latency: TopItem[];
  uptime_ranking: UptimeHost[];
  nodeglow_uptime: string;
  anomalies: Anomaly[];
  warnings: ResourceWarning[];
}

export interface HostStat {
  host: { id: number; name: string; hostname: string; source: string; check_type: string; maintenance: boolean };
  online: boolean | null;
  latency: number | null;
  sparkline: number[];
  uptime_pct: number;
  avg_latency: number | null;
  health_score: number;
  uptime_stats?: { h24: number; d7: number; d30: number };
}

export interface IntHealth {
  type: string;
  label: string;
  color: string;
  name: string;
  config_id: number;
  ok: boolean;
  error: string | null;
  last_check: string | null;
  single_instance: boolean;
  health_score?: number;
}

export interface SyslogStats {
  total_24h: number;
  errors_24h: number;
  rate_data: { labels: string[]; errors: number[]; warnings: number[]; info: number[] };
}

export interface SpeedtestData {
  download_mbps: number;
  upload_mbps: number;
  ping_ms: number;
  server_name: string;
  timestamp: string;
}

export interface StoragePool {
  name: string;
  source: string;
  healthy: boolean;
  pct: number;
  used_gb: number;
  total_gb: number;
  days_until_full?: number;
  trend_pct_per_day?: number;
}

export interface ContainerData {
  environments: ContainerEnv[];
  running: number;
  stopped: number;
}

export interface ContainerEnv {
  name: string;
  containers_running: number;
  containers_stopped: number;
  containers: ContainerInfo[];
}

export interface ContainerInfo {
  name: string;
  status: string;
  image: string;
}

export interface UpsData {
  units: UpsUnit[];
  on_battery: boolean;
}

export interface UpsUnit {
  name: string;
  status_label: string;
  on_battery: boolean;
  battery_pct: number;
  load_pct: number;
  runtime_s: number;
  model: string;
}

export interface SslCert {
  host_id: number;
  name: string;
  days: number | null;
}

export interface RecentIncident {
  id: number;
  title: string;
  severity: string;
  status: string;
  created_at: string;
}

export interface IncidentTrendDay {
  date: string;
  critical: number;
  warning: number;
  info: number;
}

export interface HeatmapHost {
  host_id: number;
  name: string;
  days: (number | null)[];
}

export interface WidgetLayout {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TopItem {
  id: number;
  name: string;
  value: number;
}

export interface UptimeHost {
  host_id: number;
  name: string;
  uptime: number;
}

export interface Anomaly {
  name: string;
  type: string;
  node: string;
  cluster_name: string;
  metric: string;
  current: number;
  mean: number;
  factor: number | null;
  sustained: number | null;
  severity: number;
  host_id?: number;
}

export interface ResourceWarning {
  name: string;
  type: string;
  node: string;
  cluster_name: string;
  metric: string;
  current: number;
  threshold: number;
}

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () =>
      get<DashboardData>('/api/dashboard'),
    refetchInterval: 30_000,
  });
}

export function useNavCounts() {
  return useQuery({
    queryKey: ['nav-counts'],
    queryFn: () => get<Record<string, number>>('/api/v2/nav-counts'),
    refetchInterval: 60_000,
  });
}
