'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { useTopology } from '@/hooks/queries/useTopology';
import { Network, Server, Wrench, ChevronRight } from 'lucide-react';

/* ── Types ── */

interface TopoNode {
  id: number;
  name: string;
  hostname: string;
  status: 'up' | 'down';
  check_type: string;
  source: string;
  maintenance: boolean;
}

interface TopoEdge {
  source: number;
  target: number;
}

/* ── Build tree structure ── */

interface TreeNode {
  node: TopoNode;
  children: TreeNode[];
}

function buildTree(nodes: TopoNode[], edges: TopoEdge[]): TreeNode[] {
  const childToParent = new Map<number, number>();
  for (const e of edges) childToParent.set(e.target, e.source);

  const nodeMap = new Map<number, TopoNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  // Find roots (nodes that are not children of anything)
  const childIds = new Set(edges.map((e) => e.target));
  const roots = nodes.filter((n) => !childIds.has(n.id));

  // Build tree recursively
  const parentToChildren = new Map<number, number[]>();
  for (const e of edges) {
    if (!parentToChildren.has(e.source)) parentToChildren.set(e.source, []);
    parentToChildren.get(e.source)!.push(e.target);
  }

  function buildSubtree(n: TopoNode): TreeNode {
    const childIds = parentToChildren.get(n.id) ?? [];
    const children = childIds
      .map((id) => nodeMap.get(id))
      .filter((c): c is TopoNode => !!c)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((c) => buildSubtree(c));
    return { node: n, children };
  }

  // Sort roots: those with children first, then alphabetical
  const sorted = roots.sort((a, b) => {
    const ac = (parentToChildren.get(a.id) ?? []).length;
    const bc = (parentToChildren.get(b.id) ?? []).length;
    if (ac !== bc) return bc - ac;
    return a.name.localeCompare(b.name);
  });

  return sorted.map((r) => buildSubtree(r));
}

/* ── Status helpers ── */

function statusColor(n: TopoNode) {
  if (n.maintenance) return { dot: 'bg-amber-400', ring: 'ring-amber-400/20', text: 'text-amber-400', bg: 'bg-amber-500/5 border-amber-500/20' };
  if (n.status === 'down') return { dot: 'bg-red-400', ring: 'ring-red-400/20', text: 'text-red-400', bg: 'bg-red-500/5 border-red-500/20' };
  return { dot: 'bg-emerald-400', ring: 'ring-emerald-400/20', text: 'text-emerald-400', bg: 'bg-emerald-500/5 border-emerald-500/20' };
}

function statusLabel(n: TopoNode) {
  if (n.maintenance) return 'Maintenance';
  if (n.status === 'down') return 'Offline';
  return 'Online';
}

/* ── Tree Node Component ── */

function TreeNodeCard({ tree, depth = 0 }: { tree: TreeNode; depth?: number }) {
  const { node, children } = tree;
  const s = statusColor(node);
  const hasChildren = children.length > 0;
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className={depth > 0 ? 'ml-6 sm:ml-10' : ''}>
      {/* Connector line */}
      {depth > 0 && (
        <div className="flex items-center gap-0 -mb-[1px] ml-[-20px] sm:ml-[-36px]">
          <div className="w-5 sm:w-9 border-b border-l border-white/[0.08] h-5 rounded-bl-lg" />
        </div>
      )}

      {/* Node card */}
      <div className={`group relative rounded-lg border transition-all hover:border-white/[0.15] ${s.bg}`}>
        <div className="flex items-center gap-3 px-3 py-2.5">
          {/* Status dot */}
          <div className="relative shrink-0">
            <span className={`block w-2.5 h-2.5 rounded-full ${s.dot}`} />
            {node.status === 'down' && !node.maintenance && (
              <span className={`absolute inset-0 w-2.5 h-2.5 rounded-full ${s.dot} animate-ping opacity-40`} />
            )}
          </div>

          {/* Info */}
          <Link href={`/hosts/${node.id}`} className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium truncate group-hover:text-sky-400 transition-colors" style={{ color: 'var(--ng-text-primary)' }}>
                {node.name}
              </span>
              <span className={`text-[10px] font-medium ${s.text}`}>{statusLabel(node)}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-[11px] text-slate-500 truncate font-mono">{node.hostname}</span>
              <span className="text-[10px] text-slate-600">{node.check_type}</span>
              {node.source !== 'manual' && (
                <span className="text-[10px] text-slate-600">via {node.source}</span>
              )}
            </div>
          </Link>

          {/* Badges + expand */}
          <div className="flex items-center gap-1.5 shrink-0">
            {node.maintenance && <Wrench size={12} className="text-amber-400" />}
            {hasChildren && (
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="p-1 rounded hover:bg-white/[0.08] transition-colors text-slate-500 hover:text-slate-300"
                title={collapsed ? 'Expand' : 'Collapse'}
              >
                <ChevronRight size={14} className={`transition-transform ${collapsed ? '' : 'rotate-90'}`} />
              </button>
            )}
          </div>
        </div>

        {/* Children count badge */}
        {hasChildren && collapsed && (
          <div className="absolute -right-2 -top-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 text-[10px] font-bold border border-sky-500/30">
              {children.length}
            </span>
          </div>
        )}
      </div>

      {/* Children */}
      {hasChildren && !collapsed && (
        <div className="space-y-1.5 mt-1.5">
          {children.map((child) => (
            <TreeNodeCard key={child.node.id} tree={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Page ── */

export default function TopologyPage() {
  useEffect(() => { document.title = 'Topology | Nodeglow'; }, []);
  const { data, isLoading } = useTopology();

  const tree = useMemo(() => {
    if (!data) return [];
    return buildTree(data.nodes, data.edges);
  }, [data]);

  const onlineCount = data?.nodes.filter((n) => n.status === 'up' && !n.maintenance).length ?? 0;
  const offlineCount = data?.nodes.filter((n) => n.status === 'down' && !n.maintenance).length ?? 0;
  const maintCount = data?.nodes.filter((n) => n.maintenance).length ?? 0;

  const connectedCount = data?.edges.length ?? 0;

  return (
    <div>
      <PageHeader title="Network Topology" description="Hierarchical view of monitored infrastructure" />

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
        {[
          { label: 'Nodes', value: data?.nodes.length ?? 0, color: 'var(--ng-text-primary)' },
          { label: 'Online', value: onlineCount, color: '#34d399' },
          { label: 'Offline', value: offlineCount, color: '#f87171' },
          { label: 'Maintenance', value: maintCount, color: '#fbbf24' },
          { label: 'Connections', value: connectedCount, color: '#38bdf8' },
        ].map((s) => (
          <GlassCard key={s.label} className="p-3 text-center">
            <p className="text-xl font-bold font-mono" style={{ color: s.color }}>{s.value}</p>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">{s.label}</p>
          </GlassCard>
        ))}
      </div>

      {isLoading ? (
        <GlassCard className="p-4 space-y-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
        </GlassCard>
      ) : !data || data.nodes.length === 0 ? (
        <GlassCard className="p-8">
          <div className="flex flex-col items-center justify-center text-slate-500">
            <Network size={48} className="mb-3 opacity-20" />
            <p className="text-sm">No topology data</p>
            <p className="text-xs mt-1 text-slate-600">Add hosts and integrations to build the hierarchy</p>
          </div>
        </GlassCard>
      ) : (
        <div className="space-y-6">
          {/* Trees with parents */}
          {tree.filter((t) => t.children.length > 0).length > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium mb-4 flex items-center gap-2" style={{ color: 'var(--ng-text-primary)' }}>
                <Network size={14} className="text-sky-400" />
                Connected Devices
              </h3>
              <div className="space-y-3">
                {tree.filter((t) => t.children.length > 0).map((t) => (
                  <TreeNodeCard key={t.node.id} tree={t} />
                ))}
              </div>
            </GlassCard>
          )}

          {/* Standalone nodes (no parent, no children) */}
          {tree.filter((t) => t.children.length === 0).length > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium mb-4 flex items-center gap-2" style={{ color: 'var(--ng-text-primary)' }}>
                <Server size={14} className="text-slate-400" />
                Standalone Hosts
                <span className="text-xs text-slate-500 font-normal">({tree.filter((t) => t.children.length === 0).length})</span>
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {tree.filter((t) => t.children.length === 0).map((t) => {
                  const n = t.node;
                  const s = statusColor(n);
                  return (
                    <Link
                      key={n.id}
                      href={`/hosts/${n.id}`}
                      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-all hover:border-white/[0.15] ${s.bg}`}
                    >
                      <div className="relative shrink-0">
                        <span className={`block w-2.5 h-2.5 rounded-full ${s.dot}`} />
                        {n.status === 'down' && !n.maintenance && (
                          <span className={`absolute inset-0 w-2.5 h-2.5 rounded-full ${s.dot} animate-ping opacity-40`} />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate" style={{ color: 'var(--ng-text-primary)' }}>{n.name}</p>
                        <p className="text-[11px] text-slate-500 truncate font-mono">{n.hostname}</p>
                      </div>
                      <span className={`text-[10px] font-medium ${s.text}`}>{statusLabel(n)}</span>
                    </Link>
                  );
                })}
              </div>
            </GlassCard>
          )}
        </div>
      )}
    </div>
  );
}
