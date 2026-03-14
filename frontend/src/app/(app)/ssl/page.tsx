'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { ShieldCheck, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

interface SslCert {
  id: number;
  name: string;
  hostname: string;
  enabled: boolean;
  days: number | null;
}

interface SslData {
  certs: SslCert[];
  expiring_soon: number;
}

function expiryBadge(days: number | null): { severity: 'critical' | 'warning' | 'info'; label: string } {
  if (days === null) return { severity: 'warning', label: 'Unknown' };
  if (days <= 7) return { severity: 'critical', label: `${days}d` };
  if (days <= 30) return { severity: 'warning', label: `${days}d` };
  return { severity: 'info', label: `${days}d` };
}

function expiryColor(days: number | null): string {
  if (days === null) return 'text-slate-500';
  if (days <= 7) return 'text-red-400';
  if (days <= 30) return 'text-amber-400';
  return 'text-emerald-400';
}

export default function SslPage() {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ['ssl-certs'],
    queryFn: () => get<SslData>('/api/ssl/certs'),
  });

  const certs = data?.certs ?? [];
  const expiringSoon = data?.expiring_soon ?? 0;

  async function refreshAll() {
    setRefreshing(true);
    try {
      await post('/api/ssl/refresh-all');
      qc.invalidateQueries({ queryKey: ['ssl-certs'] });
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="SSL Certificates"
        description="Certificate expiry monitoring"
        actions={
          <Button variant="ghost" size="sm" onClick={refreshAll} disabled={refreshing}>
            <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
            {refreshing ? 'Refreshing...' : 'Refresh All'}
          </Button>
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-slate-100">{certs.length}</p>
          <p className="text-xs text-slate-400 mt-1">HTTPS Hosts</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className={`text-2xl font-semibold ${expiringSoon > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>
            {expiringSoon}
          </p>
          <p className="text-xs text-slate-400 mt-1">Expiring Soon (&le;30d)</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-emerald-400">
            {certs.filter(c => c.days !== null && c.days > 30).length}
          </p>
          <p className="text-xs text-slate-400 mt-1">Healthy</p>
        </GlassCard>
      </div>

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Hostname</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Days Until Expiry</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 mx-auto" /></td>
                  </tr>
                ))}
              {certs.map((c) => {
                const badge = expiryBadge(c.days);
                return (
                  <tr key={c.id} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/hosts/${c.id}`} className="flex items-center gap-2 text-slate-200 hover:text-sky-400">
                        <ShieldCheck size={14} className={expiryColor(c.days)} />
                        {c.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{c.hostname}</td>
                    <td className={`px-4 py-3 text-right font-mono ${expiryColor(c.days)}`}>
                      {c.days !== null ? c.days : '—'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <Badge variant="severity" severity={badge.severity}>
                        {badge.label}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
              {!isLoading && certs.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                    No HTTPS hosts configured
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}
