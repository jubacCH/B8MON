'use client';

import { useEffect, useMemo, useRef, useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { useTopology } from '@/hooks/queries/useTopology';
import { Network, ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';

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

/* ── Tree builder ── */

interface TreeNode {
  node: TopoNode;
  children: TreeNode[];
}

function buildTrees(nodes: TopoNode[], edges: TopoEdge[]): { trees: TreeNode[]; orphans: TopoNode[] } {
  const nodeMap = new Map<number, TopoNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  const childIds = new Set(edges.map((e) => e.target));
  const parentToChildren = new Map<number, number[]>();
  for (const e of edges) {
    if (!parentToChildren.has(e.source)) parentToChildren.set(e.source, []);
    parentToChildren.get(e.source)!.push(e.target);
  }

  function build(n: TopoNode): TreeNode {
    const cIds = parentToChildren.get(n.id) ?? [];
    const children = cIds
      .map((id) => nodeMap.get(id))
      .filter((c): c is TopoNode => !!c)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((c) => build(c));
    return { node: n, children };
  }

  const roots = nodes.filter((n) => !childIds.has(n.id));
  const withChildren = roots.filter((r) => (parentToChildren.get(r.id) ?? []).length > 0);
  const orphans = roots.filter((r) => (parentToChildren.get(r.id) ?? []).length === 0);

  const trees = withChildren
    .sort((a, b) => (parentToChildren.get(b.id)?.length ?? 0) - (parentToChildren.get(a.id)?.length ?? 0) || a.name.localeCompare(b.name))
    .map((r) => build(r));

  return { trees, orphans: orphans.sort((a, b) => a.name.localeCompare(b.name)) };
}

/* ── Layout engine: compute x,y for each node ── */

const NODE_W = 200;
const NODE_H = 64;
const GAP_X = 40;
const GAP_Y = 90;

interface LayoutNode {
  id: number;
  x: number;
  y: number;
  node: TopoNode;
  parentId: number | null;
}

function layoutTree(tree: TreeNode, offsetX: number): { nodes: LayoutNode[]; width: number } {
  const result: LayoutNode[] = [];

  function measure(t: TreeNode): number {
    if (t.children.length === 0) return NODE_W;
    return t.children.reduce((sum, c) => sum + measure(c) + GAP_X, -GAP_X);
  }

  function place(t: TreeNode, x: number, y: number, parentId: number | null) {
    const totalW = measure(t);
    const cx = x + totalW / 2 - NODE_W / 2;
    result.push({ id: t.node.id, x: cx, y, node: t.node, parentId });

    let childX = x;
    for (const child of t.children) {
      const childW = measure(child);
      place(child, childX, y + NODE_H + GAP_Y, t.node.id);
      childX += childW + GAP_X;
    }
  }

  const totalW = measure(tree);
  place(tree, offsetX, 0, null);
  return { nodes: result, width: totalW };
}

/* ── Colors ── */

function nodeColors(n: TopoNode) {
  if (n.maintenance) return { fill: '#78350f', stroke: '#f59e0b', text: '#fbbf24', dot: '#fbbf24', line: '#78350f' };
  if (n.status === 'down') return { fill: '#450a0a', stroke: '#dc2626', text: '#f87171', dot: '#f87171', line: '#7f1d1d' };
  return { fill: '#022c22', stroke: '#059669', text: '#34d399', dot: '#34d399', line: '#064e3b' };
}

/* ── Canvas-based topology map ── */

function TopologyCanvas({
  trees,
  orphans,
}: {
  trees: TreeNode[];
  orphans: TopoNode[];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 40, y: 40 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [hoveredId, setHoveredId] = useState<number | null>(null);

  // Layout all trees side by side
  const { allNodes, edges, totalW, totalH } = useMemo(() => {
    const allNodes: LayoutNode[] = [];
    const edges: { from: LayoutNode; to: LayoutNode }[] = [];
    let offsetX = 0;
    let maxH = 0;

    for (const tree of trees) {
      const { nodes, width } = layoutTree(tree, offsetX);
      allNodes.push(...nodes);
      offsetX += width + GAP_X * 3;

      // Find max depth
      const maxY = Math.max(...nodes.map((n) => n.y));
      if (maxY + NODE_H > maxH) maxH = maxY + NODE_H;
    }

    // Place orphans in a grid below the trees
    if (orphans.length > 0) {
      const orphanY = maxH + GAP_Y * 1.5;
      const cols = Math.max(Math.ceil(Math.sqrt(orphans.length * 2)), 4);
      orphans.forEach((o, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        allNodes.push({
          id: o.id,
          x: col * (NODE_W + GAP_X / 2),
          y: orphanY + row * (NODE_H + GAP_X / 2),
          node: o,
          parentId: null,
        });
      });
      const orphanRows = Math.ceil(orphans.length / cols);
      maxH = orphanY + orphanRows * (NODE_H + GAP_X / 2);
    }

    // Build edges
    const nodeById = new Map(allNodes.map((n) => [n.id, n]));
    for (const n of allNodes) {
      if (n.parentId != null) {
        const parent = nodeById.get(n.parentId);
        if (parent) edges.push({ from: parent, to: n });
      }
    }

    return { allNodes, edges, totalW: offsetX || NODE_W, totalH: maxH + NODE_H };
  }, [trees, orphans]);

  // Draw
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const ctx = canvas.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(zoom, zoom);

    // Draw edges
    for (const { from, to } of edges) {
      const fromX = from.x + NODE_W / 2;
      const fromY = from.y + NODE_H;
      const toX = to.x + NODE_W / 2;
      const toY = to.y;
      const midY = fromY + (toY - fromY) / 2;

      const colors = nodeColors(to.node);
      ctx.strokeStyle = colors.line;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(fromX, fromY);
      ctx.bezierCurveTo(fromX, midY, toX, midY, toX, toY);
      ctx.stroke();

      // Small dot at connection point
      ctx.fillStyle = colors.dot;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.arc(toX, toY, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // Draw nodes
    for (const ln of allNodes) {
      const c = nodeColors(ln.node);
      const isHovered = hoveredId === ln.id;
      const r = 10;

      // Shadow for hovered
      if (isHovered) {
        ctx.shadowColor = c.dot;
        ctx.shadowBlur = 20;
      }

      // Background
      ctx.fillStyle = isHovered ? c.stroke + '30' : c.fill;
      ctx.strokeStyle = isHovered ? c.dot : c.stroke + '80';
      ctx.lineWidth = isHovered ? 2.5 : 1.5;
      ctx.beginPath();
      ctx.roundRect(ln.x, ln.y, NODE_W, NODE_H, r);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Status dot
      ctx.fillStyle = c.dot;
      ctx.beginPath();
      ctx.arc(ln.x + 18, ln.y + NODE_H / 2, 5, 0, Math.PI * 2);
      ctx.fill();

      // Glow ring
      ctx.strokeStyle = c.dot;
      ctx.globalAlpha = 0.3;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(ln.x + 18, ln.y + NODE_H / 2, 9, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;

      // Name — large and clear
      ctx.fillStyle = isHovered ? '#f8fafc' : '#f1f5f9';
      ctx.font = `600 14px ui-sans-serif, system-ui, -apple-system, sans-serif`;
      ctx.textBaseline = 'top';
      const maxNameLen = Math.floor((NODE_W - 46) / 8);
      const name = ln.node.name.length > maxNameLen ? ln.node.name.slice(0, maxNameLen - 1) + '…' : ln.node.name;
      ctx.fillText(name, ln.x + 34, ln.y + 13);

      // Hostname — secondary info
      ctx.fillStyle = '#94a3b8';
      ctx.font = `400 11px ui-monospace, SFMono-Regular, monospace`;
      const maxHostLen = Math.floor((NODE_W - 46) / 6.5);
      const host = ln.node.hostname.length > maxHostLen ? ln.node.hostname.slice(0, maxHostLen - 1) + '…' : ln.node.hostname;
      ctx.fillText(host, ln.x + 34, ln.y + 35);

      // Status text right side
      const statusText = ln.node.maintenance ? 'MAINT' : ln.node.status === 'up' ? 'UP' : 'DOWN';
      ctx.fillStyle = c.dot;
      ctx.globalAlpha = 0.7;
      ctx.font = `700 9px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = 'right';
      ctx.fillText(statusText, ln.x + NODE_W - 10, ln.y + 14);
      ctx.textAlign = 'left';
      ctx.globalAlpha = 1;
    }

    ctx.restore();
  }, [allNodes, edges, pan, zoom, hoveredId]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const obs = new ResizeObserver(() => draw());
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, [draw]);

  // Hit test
  const hitTest = useCallback((clientX: number, clientY: number): number | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const mx = (clientX - rect.left - pan.x) / zoom;
    const my = (clientY - rect.top - pan.y) / zoom;
    for (const ln of allNodes) {
      if (mx >= ln.x && mx <= ln.x + NODE_W && my >= ln.y && my <= ln.y + NODE_H) {
        return ln.id;
      }
    }
    return null;
  }, [allNodes, pan, zoom]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    setDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  }, [pan]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragging) {
      setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
    }
    const id = hitTest(e.clientX, e.clientY);
    setHoveredId(id);
    if (canvasRef.current) {
      canvasRef.current.style.cursor = id != null ? 'pointer' : dragging ? 'grabbing' : 'grab';
    }
  }, [dragging, dragStart, hitTest]);

  const handleMouseUp = useCallback(() => setDragging(false), []);

  const handleClick = useCallback((e: React.MouseEvent) => {
    const id = hitTest(e.clientX, e.clientY);
    if (id != null) router.push(`/hosts/${id}`);
  }, [hitTest, router]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom((z) => Math.max(0.3, Math.min(3, z * delta)));
  }, []);

  const fitView = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const padding = 60;
    const scaleX = (rect.width - padding * 2) / totalW;
    const scaleY = (rect.height - padding * 2) / totalH;
    const newZoom = Math.min(scaleX, scaleY, 1.5);
    setZoom(newZoom);
    setPan({
      x: (rect.width - totalW * newZoom) / 2,
      y: padding,
    });
  }, [totalW, totalH]);

  // Fit on first render
  useEffect(() => { fitView(); }, [fitView]);

  return (
    <div className="relative" ref={containerRef}>
      <canvas
        ref={canvasRef}
        className="w-full"
        style={{ height: 650 }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => { setDragging(false); setHoveredId(null); }}
        onClick={handleClick}
        onWheel={handleWheel}
      />

      {/* Controls */}
      <div className="absolute top-3 right-3 flex flex-col gap-1">
        <button onClick={() => setZoom((z) => Math.min(3, z * 1.2))} className="p-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] text-slate-400 transition-colors" title="Zoom in">
          <ZoomIn size={14} />
        </button>
        <button onClick={() => setZoom((z) => Math.max(0.3, z / 1.2))} className="p-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] text-slate-400 transition-colors" title="Zoom out">
          <ZoomOut size={14} />
        </button>
        <button onClick={fitView} className="p-1.5 rounded-md bg-white/[0.06] hover:bg-white/[0.12] text-slate-400 transition-colors" title="Fit to view">
          <Maximize2 size={14} />
        </button>
      </div>

      {/* Hint */}
      <div className="absolute bottom-3 left-3 text-[10px] text-slate-600">
        Drag to pan · Scroll to zoom · Click node to open
      </div>

      {/* Legend */}
      <div className="absolute bottom-3 right-3 flex items-center gap-3">
        {[
          { color: '#34d399', label: 'Online' },
          { color: '#f87171', label: 'Offline' },
          { color: '#fbbf24', label: 'Maintenance' },
        ].map((l) => (
          <div key={l.label} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ background: l.color }} />
            <span className="text-[10px] text-slate-500">{l.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Page ── */

export default function TopologyPage() {
  useEffect(() => { document.title = 'Topology | Nodeglow'; }, []);
  const { data, isLoading } = useTopology();

  const { trees, orphans } = useMemo(() => {
    if (!data) return { trees: [], orphans: [] };
    return buildTrees(data.nodes, data.edges);
  }, [data]);

  const onlineCount = data?.nodes.filter((n) => n.status === 'up' && !n.maintenance).length ?? 0;
  const offlineCount = data?.nodes.filter((n) => n.status === 'down' && !n.maintenance).length ?? 0;
  const maintCount = data?.nodes.filter((n) => n.maintenance).length ?? 0;
  const connectedCount = data?.edges.length ?? 0;

  return (
    <div>
      <PageHeader title="Network Topology" description="Infrastructure dependency map" />

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

      {/* Topology map */}
      <GlassCard className="overflow-hidden">
        {isLoading ? (
          <Skeleton className="w-full h-[650px]" />
        ) : !data || data.nodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-[650px] text-slate-500">
            <Network size={48} className="mb-3 opacity-20" />
            <p className="text-sm">No topology data</p>
            <p className="text-xs mt-1 text-slate-600">Add hosts and integrations to build the map</p>
          </div>
        ) : (
          <TopologyCanvas trees={trees} orphans={orphans} />
        )}
      </GlassCard>
    </div>
  );
}
