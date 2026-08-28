import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { HostStatus, HostDetail, PingResult } from '@/types';

export function useHosts() {
  return useQuery({
    queryKey: ['hosts'],
    queryFn: () => get<HostStatus[]>('/hosts/api/status'),
    refetchInterval: 30_000,
  });
}

export interface HostListItem {
  id: number;
  name: string;
  hostname: string;
  status: 'online' | 'offline' | 'maintenance' | 'disabled' | 'unknown';
  check_type: string;
  port: number | null;
  source: string;
  source_detail: string | null;
  latency_ms: number | null;
  last_check: string | null;
  uptime: { h24: number | null; d7: number | null; d30: number | null };
  maintenance: boolean;
  enabled: boolean;
  /** Agent responsible for checking this host, or null when the core checks it. */
  probe_id: number | null;
}

/** Host list from the v1 API — used where probe assignment is needed. */
export function useHostsV1() {
  return useQuery({
    queryKey: ['hosts-v1'],
    queryFn: () => get<HostListItem[]>('/api/v1/hosts'),
    refetchInterval: 30_000,
  });
}

export function useHost(id: number) {
  return useQuery({
    queryKey: ['host', id],
    queryFn: () => get<HostDetail>(`/api/v1/hosts/${id}`),
    enabled: id > 0,
  });
}

export function useHostHistory(id: number, hours = 24) {
  return useQuery({
    queryKey: ['host-history', id, hours],
    queryFn: () =>
      get<{ host_id: number; count: number; results: PingResult[] }>(
        `/api/v1/hosts/${id}/history?hours=${hours}`,
      ),
    enabled: id > 0,
  });
}

export type TimelineEventType = 'status' | 'incident' | 'syslog';
export type TimelineSeverity = 'critical' | 'error' | 'warning' | 'info';

export interface TimelineEvent {
  ts: string;
  type: TimelineEventType;
  severity: TimelineSeverity;
  title: string;
  summary: string | null;
  details: Record<string, unknown>;
}

export interface TimelineResponse {
  host_id: number;
  host_name: string | null;
  hours: number;
  since: string;
  sources: TimelineEventType[];
  total: number;
  events: TimelineEvent[];
}

export function useHostTimeline(
  id: number,
  hours: number,
  sources: TimelineEventType[],
) {
  const sourcesParam = [...sources].sort().join(',');
  return useQuery({
    queryKey: ['host-timeline', id, hours, sourcesParam],
    queryFn: () =>
      get<TimelineResponse>(
        `/api/v1/hosts/${id}/timeline?hours=${hours}&sources=${sourcesParam}&limit=300`,
      ),
    enabled: id > 0 && sources.length > 0,
    // Auto-refresh only on the 1h view — see design decision #6.
    refetchInterval: hours <= 1 ? 30_000 : false,
  });
}
