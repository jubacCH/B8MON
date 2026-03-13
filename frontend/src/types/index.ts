/** Shared types for the Nodeglow frontend */

// ── Auth ──
export interface User {
  id: number;
  username: string;
  role: 'admin' | 'editor' | 'readonly';
}

// ── Hosts ──
export interface PingHost {
  id: number;
  name: string;
  hostname: string;
  enabled: boolean;
  check_type: string;
  port: number | null;
  latency_threshold_ms: number | null;
  maintenance: boolean;
  maintenance_until: string | null;
  ssl_expiry_days: number | null;
  source: 'manual' | 'phpipam' | 'proxmox' | 'unifi' | 'agent';
  source_detail: string | null;
  mac_address: string | null;
  parent_id: number | null;
  created_at: string;
}

export interface HostStatus {
  id: number;
  name: string;
  hostname: string;
  online: boolean | null;
  latency_ms: number | null;
  check_type: string;
  enabled: boolean;
  maintenance: boolean;
  source: string;
}

export interface HostDetail extends PingHost {
  latest: { success: boolean; latency_ms: number | null; timestamp: string } | null;
  uptime: { h24: number; d7: number; d30: number };
  heatmap: HeatmapDay[];
}

export interface HeatmapDay {
  date: string;
  pct: number | null;
  color: 'emerald' | 'yellow' | 'red' | 'gray';
}

export interface PingResult {
  id: number;
  host_id: number;
  timestamp: string;
  success: boolean;
  latency_ms: number | null;
}

// ── Agents ──
export interface Agent {
  id: number;
  name: string;
  hostname: string | null;
  platform: string | null;
  arch: string | null;
  agent_version: string | null;
  enabled: boolean;
  last_seen: string | null;
  created_at: string;
  cpu_pct: number | null;
  mem_pct: number | null;
  disk_pct: number | null;
}

export interface AgentSnapshot {
  agent_id: number;
  timestamp: string;
  cpu_pct: number | null;
  mem_pct: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  disk_pct: number | null;
  uptime_s: number | null;
  data_json: Record<string, unknown> | null;
}

// ── Integrations ──
export interface IntegrationConfig {
  id: number;
  type: string;
  name: string;
  enabled: boolean;
  created_at: string;
}

export interface IntegrationSnapshot {
  id: number;
  entity_type: string;
  entity_id: number;
  timestamp: string;
  ok: boolean;
  data_json: Record<string, unknown> | null;
  error: string | null;
}

export interface IntegrationMeta {
  label: string;
  color: string;
  url_prefix: string;
  single_instance: boolean;
}

// ── Incidents ──
export interface Incident {
  id: number;
  rule: string;
  title: string;
  severity: 'critical' | 'warning' | 'info';
  status: 'open' | 'acknowledged' | 'resolved';
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  acknowledged_by: string | null;
}

export interface IncidentEvent {
  id: number;
  incident_id: number;
  timestamp: string;
  event_type: string;
  summary: string;
  detail: string | null;
}

// ── Syslog ──
export interface SyslogMessage {
  timestamp: string;
  severity: number;
  severity_label: string;
  hostname: string;
  source_ip: string;
  app_name: string;
  message: string;
  host_id: number | null;
  tags: string[];
  noise_score: number;
  template_hash: string;
}

export interface LogTemplate {
  id: number;
  template_hash: string;
  template: string;
  example: string | null;
  count: number;
  first_seen: string;
  last_seen: string;
  noise_score: number;
  tags: string;
}

// ── Alert Rules ──
export interface AlertRule {
  id: number;
  name: string;
  enabled: boolean;
  source_type: string;
  source_id: number | null;
  field_path: string;
  operator: string;
  threshold: string | null;
  severity: 'critical' | 'warning' | 'info';
  notify_channels: string | null;
  message_template: string | null;
  cooldown_minutes: number;
  last_triggered_at: string | null;
  created_at: string;
}

// ── API Keys ──
export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  role: 'readonly' | 'editor' | 'admin';
  enabled: boolean;
  last_used: string | null;
  created_at: string | null;
}

// ── WebSocket ──
export interface WsPingUpdate {
  type: 'ping_update';
  ts: string;
  host_id: number;
  name: string;
  online: boolean;
  latency_ms: number | null;
}

export interface WsAgentMetric {
  type: 'agent_metric';
  ts: string;
  agent_id: number;
  agent_name: string;
  cpu_pct: number;
  mem_pct: number;
  disk_pct: number;
}

export type WsMessage = WsPingUpdate | WsAgentMetric;

// ── Dashboard ──
export interface DashboardLayout {
  widgets: WidgetConfig[];
}

export interface WidgetConfig {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
}

// ── Notifications ──
export interface NotificationLog {
  id: number;
  timestamp: string;
  channel: string;
  title: string;
  message: string | null;
  severity: string;
  status: 'sent' | 'failed';
  error: string | null;
}

// ── Digest ──
export interface Digest {
  period_start: string;
  period_end: string;
  incidents: {
    total: number;
    by_severity: Record<string, number>;
    mttr_minutes: number | null;
    top: { id: number; title: string; severity: string }[];
  };
  hosts: {
    avg_uptime: number;
    worst: { id: number; name: string; uptime: number; failures: number }[];
  };
  syslog: {
    total: number;
    errors: number;
    top_errors: { template: string; count: number }[];
  };
}

// ── Nav Counts ──
export interface NavCounts {
  hosts: number;
  alerts: number;
  rules: number;
  agents: number;
  ssl: number;
  credentials: number;
  integrations: Record<string, number>;
}
