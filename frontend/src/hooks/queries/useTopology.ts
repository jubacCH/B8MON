import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';

interface TopologyNode {
  id: number;
  name: string;
  hostname: string;
  // 'unknown' covers a host nobody is currently observing (e.g. its probe
  // went silent) — distinct from both 'up' and 'down'.
  status: 'up' | 'down' | 'unknown';
  check_type: string;
  source: string;
  maintenance: boolean;
}

interface TopologyEdge {
  source: number;
  target: number;
}

interface TopologyData {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export function useTopology() {
  return useQuery({
    queryKey: ['topology'],
    queryFn: () => get<TopologyData>('/api/v1/topology'),
    refetchInterval: 30_000,
  });
}
