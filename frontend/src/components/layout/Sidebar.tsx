'use client';

import { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useThemeStore } from '@/stores/theme';
import { useAuthStore } from '@/stores/auth';
import { useDashboard, useNavCounts } from '@/hooks/queries/useDashboard';
import {
  LayoutDashboard, Server, AlertTriangle, Bell, FileText,
  Bot, Scan, Radio, ShieldCheck, KeyRound, ChevronDown,
  Settings, Users, Activity, BookOpen, Search, LogOut, Plus,
  ClipboardList, Sun, Moon,
} from 'lucide-react';

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
  iconColor?: string;
  adminOnly?: boolean;
  countKey?: string;
}

const mainNav: NavItem[] = [
  { label: 'Dashboard', href: '/', icon: LayoutDashboard, iconColor: 'text-sky-400' },
  { label: 'Hosts', href: '/hosts', icon: Server, iconColor: 'text-emerald-400', countKey: 'hosts' },
  { label: 'Alerts', href: '/alerts', icon: AlertTriangle, iconColor: 'text-amber-400', countKey: 'alerts' },
  { label: 'Rules', href: '/rules', icon: Bell, iconColor: 'text-orange-400', countKey: 'rules' },
  { label: 'Syslog', href: '/syslog', icon: FileText, iconColor: 'text-violet-400' },
  { label: 'Agents', href: '/agents', icon: Bot, iconColor: 'text-cyan-400', countKey: 'agents' },
  { label: 'Scanner', href: '/scanner', icon: Scan, iconColor: 'text-indigo-400' },
  { label: 'SNMP', href: '/snmp', icon: Radio, iconColor: 'text-teal-400' },
  { label: 'SSL', href: '/ssl', icon: ShieldCheck, iconColor: 'text-green-400', countKey: 'ssl' },
  { label: 'Credentials', href: '/credentials', icon: KeyRound, iconColor: 'text-yellow-400', countKey: 'credentials' },
  { label: 'Tasks', href: '/tasks', icon: ClipboardList, iconColor: 'text-rose-400', countKey: 'tasks' },
];

const systemNav: NavItem[] = [
  { label: 'Status', href: '/system/status', icon: Activity },
  { label: 'Digest', href: '/digest', icon: BookOpen },
  { label: 'Settings', href: '/settings', icon: Settings, adminOnly: true },
  { label: 'Users', href: '/users', icon: Users, adminOnly: true },
];

const integrationTypes = [
  { slug: 'proxmox', label: 'Proxmox' },
  { slug: 'unifi', label: 'UniFi' },
  { slug: 'unas', label: 'UniFi NAS' },
  { slug: 'portainer', label: 'Portainer' },
  { slug: 'truenas', label: 'TrueNAS' },
  { slug: 'synology', label: 'Synology' },
  { slug: 'pihole', label: 'Pi-hole' },
  { slug: 'adguard', label: 'AdGuard' },
  { slug: 'firewall', label: 'Firewall' },
  { slug: 'hass', label: 'Home Assistant' },
  { slug: 'gitea', label: 'Gitea' },
  { slug: 'phpipam', label: 'phpIPAM' },
  { slug: 'speedtest', label: 'Speedtest' },
  { slug: 'ups', label: 'UPS / NUT' },
  { slug: 'redfish', label: 'Redfish' },
];

// All searchable items for global search
const allSearchItems = [
  ...mainNav.map((n) => ({ label: n.label, href: n.href })),
  ...systemNav.map((n) => ({ label: n.label, href: n.href })),
  ...integrationTypes.map((i) => ({ label: i.label, href: `/integration/${i.slug}` })),
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { sidebarCollapsed, colorMode, toggleColorMode } = useThemeStore();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [intOpen, setIntOpen] = useState(() => pathname.startsWith('/integration'));
  const [search, setSearch] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const { data: dashData } = useDashboard();
  const { data: navCounts } = useNavCounts();

  // Badge counts for nav items
  const navBadges: Record<string, { count: number; color: string }> = {};
  if (dashData) {
    const offlineCount = dashData.offline_count ?? 0;
    if (offlineCount > 0) navBadges['/hosts'] = { count: offlineCount, color: 'bg-red-500/20 text-red-400' };
    const activeInc = dashData.active_incidents ?? 0;
    if (activeInc > 0) navBadges['/alerts'] = { count: activeInc, color: 'bg-red-500/20 text-red-400' };
  }
  if (navCounts) {
    const taskCount = navCounts.tasks ?? 0;
    if (taskCount > 0) navBadges['/tasks'] = { count: taskCount, color: 'bg-amber-500/20 text-amber-400' };
  }

  // Keyboard shortcuts: Cmd+K for search, g+KEY for navigation
  useEffect(() => {
    let gPressed = false;
    let gTimeout: ReturnType<typeof setTimeout>;

    const shortcuts: Record<string, string> = {
      d: '/', h: '/hosts', a: '/alerts', s: '/syslog',
      r: '/rules', i: '/settings', t: '/system/status',
    };

    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;

      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }

      if (isInput) return;

      if (e.key === 'g' && !gPressed) {
        gPressed = true;
        clearTimeout(gTimeout);
        gTimeout = setTimeout(() => { gPressed = false; }, 500);
        return;
      }

      if (gPressed && shortcuts[e.key]) {
        e.preventDefault();
        router.push(shortcuts[e.key]);
        gPressed = false;
      }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); clearTimeout(gTimeout); };
  }, [router]);

  const searchResults = search.trim()
    ? allSearchItems.filter((item) => item.label.toLowerCase().includes(search.toLowerCase()))
    : [];

  const isAdmin = user?.role === 'admin';

  return (
    <aside
      className={cn(
        'flex flex-col h-screen border-r transition-all duration-200',
        sidebarCollapsed ? 'w-16' : 'w-[260px]',
      )}
      style={{ background: 'var(--ng-bg)', borderColor: 'var(--ng-glass-border)' }}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 h-14 border-b" style={{ borderColor: 'var(--ng-glass-border)' }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo-icon.svg" alt="Nodeglow" className="w-8 h-8" />
        {!sidebarCollapsed && (
          <span className="text-lg font-semibold bg-gradient-to-r from-sky-400 to-violet-400 bg-clip-text text-transparent">
            Nodeglow
          </span>
        )}
      </div>

      {/* Search */}
      {!sidebarCollapsed && (
        <div className="px-3 py-2 relative">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-2.5 text-slate-500" />
            <input
              ref={searchRef}
              type="text"
              placeholder="Search... ⌘K"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onFocus={() => setSearchFocused(true)}
              onBlur={() => setTimeout(() => setSearchFocused(false), 200)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && searchResults.length > 0) {
                  router.push(searchResults[0].href);
                  setSearch('');
                  searchRef.current?.blur();
                }
                if (e.key === 'Escape') {
                  setSearch('');
                  searchRef.current?.blur();
                }
              }}
              className="w-full pl-8 pr-3 py-2 text-xs rounded-md bg-white/[0.04] border border-white/[0.06] text-slate-300 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
            />
          </div>
          {searchFocused && searchResults.length > 0 && (
            <div className="absolute left-3 right-3 mt-1 z-50 rounded-md border shadow-xl overflow-hidden" style={{ background: 'var(--ng-surface)', borderColor: 'var(--ng-glass-border-elevated)' }}>
              {searchResults.slice(0, 8).map((item) => (
                <button
                  key={item.href}
                  className="w-full text-left px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors"
                  onMouseDown={() => {
                    router.push(item.href);
                    setSearch('');
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Main Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          {!sidebarCollapsed && 'Main'}
        </p>
        {mainNav.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'relative flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'nav-active'
                  : 'text-slate-400 hover:bg-white/[0.08] hover:text-slate-200',
              )}
            >
              {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-current" />}
              <item.icon size={18} className={isActive ? '' : item.iconColor || ''} />
              {!sidebarCollapsed && (
                <>
                  <span className="flex-1">{item.label}</span>
                  {navBadges[item.href] && (
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${navBadges[item.href].color}`}>
                      {navBadges[item.href].count}
                    </span>
                  )}
                </>
              )}
              {sidebarCollapsed && navBadges[item.href] && (
                <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-red-400" />
              )}
            </Link>
          );
        })}

        {/* Integrations */}
        <div className="pt-2">
          <button
            onClick={() => setIntOpen(!intOpen)}
            className="flex items-center gap-3 w-full px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-400"
          >
            {!sidebarCollapsed && <span className="flex-1 text-left">Integrations</span>}
            {!sidebarCollapsed && (
              <ChevronDown size={14} className={cn('transition-transform', intOpen && 'rotate-180')} />
            )}
          </button>
          {intOpen && !sidebarCollapsed && (
            <div className="space-y-0.5">
              {integrationTypes.map((int) => {
                const href = `/integration/${int.slug}`;
                const isActive = pathname.startsWith(href);
                return (
                  <Link
                    key={int.slug}
                    href={href}
                    className={cn(
                      'flex items-center gap-3 px-3 py-1.5 rounded-md text-sm transition-colors pl-7',
                      isActive
                        ? 'nav-active'
                        : 'text-slate-400 hover:bg-white/[0.08] hover:text-slate-200',
                    )}
                  >
                    <span className="flex-1">{int.label}</span>
                  </Link>
                );
              })}
              {isAdmin && (
                <Link
                  href="/credentials"
                  className="flex items-center gap-2 px-3 py-1.5 pl-7 text-sm text-slate-500 hover:text-sky-400 transition-colors"
                >
                  <Plus size={14} />
                  <span>Add Integration</span>
                </Link>
              )}
            </div>
          )}
        </div>

        {/* System */}
        <div className="pt-2">
          <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            {!sidebarCollapsed && 'System'}
          </p>
          {systemNav.map((item) => {
            if (item.adminOnly && !isAdmin) return null;
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'relative flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  isActive
                    ? 'nav-active'
                    : 'text-slate-400 hover:bg-white/[0.08] hover:text-slate-200',
                )}
              >
                {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-current" />}
                <item.icon size={18} />
                {!sidebarCollapsed && <span>{item.label}</span>}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* User */}
      {user && !sidebarCollapsed && (
        <div className="px-3 py-3 border-t" style={{ borderColor: 'var(--ng-glass-border)' }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-medium" style={{ background: 'var(--ng-glass-bg)', color: 'var(--ng-text-secondary)' }}>
              {user.username[0]?.toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm truncate" style={{ color: 'var(--ng-text-primary)' }}>{user.username}</p>
              <p className="text-[10px] uppercase" style={{ color: 'var(--ng-text-muted)' }}>{user.role}</p>
            </div>
            <button
              onClick={toggleColorMode}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: 'var(--ng-text-muted)' }}
              title={colorMode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {colorMode === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button
              onClick={logout}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: 'var(--ng-text-muted)' }}
              title="Logout"
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
