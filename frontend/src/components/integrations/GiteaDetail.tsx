'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { GitBranch, Star, GitFork, AlertCircle, Lock } from 'lucide-react';

interface GiteaRepo {
  name: string;
  full_name: string;
  description: string;
  stars: number;
  forks: number;
  open_issues: number;
  updated_at: string;
  private: boolean;
}

interface GiteaData {
  version: string;
  repos_total: number;
  repos_public: number;
  repos_private: number;
  repos: GiteaRepo[];
  users_total: number;
  orgs_total: number;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function GiteaDetail({ data }: { data: GiteaData }) {
  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Repositories" value={data.repos_total} />
        <StatCard label="Public" value={data.repos_public} />
        <StatCard label="Private" value={data.repos_private} />
        <StatCard label="Users" value={data.users_total} />
        <StatCard label="Organizations" value={data.orgs_total} />
      </div>

      {/* Version */}
      <GlassCard className="p-4">
        <div className="flex items-center gap-3">
          <GitBranch className="h-4 w-4 text-slate-400" />
          <span className="text-sm text-slate-300">Gitea Version</span>
          <Badge>{data.version}</Badge>
        </div>
      </GlassCard>

      {/* Repos */}
      {data.repos && data.repos.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Repositories</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Description</th>
                  <th className="px-4 py-2 text-center"><Star className="h-3 w-3 inline" /></th>
                  <th className="px-4 py-2 text-center"><GitFork className="h-3 w-3 inline" /></th>
                  <th className="px-4 py-2 text-center"><AlertCircle className="h-3 w-3 inline" /></th>
                  <th className="px-4 py-2 text-right">Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.repos.map((r) => (
                  <tr key={r.full_name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        {r.private && <Lock className="h-3 w-3 text-amber-400" />}
                        <span className="text-slate-200 font-mono text-xs">{r.full_name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2 text-slate-400 text-xs max-w-xs truncate">{r.description || '—'}</td>
                    <td className="px-4 py-2 text-center text-slate-400">{r.stars}</td>
                    <td className="px-4 py-2 text-center text-slate-400">{r.forks}</td>
                    <td className="px-4 py-2 text-center text-slate-400">{r.open_issues}</td>
                    <td className="px-4 py-2 text-right text-slate-500 text-xs">
                      {new Date(r.updated_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
