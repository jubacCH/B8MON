'use client';

import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Settings, Activity, Bell, Palette, Key,
  Plus, Trash2, Copy, Send, Clock, CheckCircle,
  XCircle, AlertTriangle,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { api, get, post, del } from '@/lib/api';
import { useToastStore } from '@/stores/toast';

/* ---------- Types ---------- */

type Tab = 'system' | 'monitoring' | 'notifications' | 'appearance' | 'api';

interface SettingsData {
  site_name: string;
  timezone: string;
  ping_interval: string;
  latency_threshold_ms: string;
  proxmox_interval: string;
  notify_enabled: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  discord_webhook_url: string;
  webhook_url: string;
  webhook_secret: string;
  smtp_host: string;
  smtp_port: string;
  smtp_user: string;
  smtp_from: string;
  smtp_to: string;
  smtp_has_pw: boolean;
}

interface ApiKeyEntry {
  id: number;
  name: string;
  prefix: string;
  role: string;
  enabled: boolean;
  last_used: string | null;
  created_at: string | null;
}

interface NotifLog {
  id: number;
  timestamp: string | null;
  channel: string;
  title: string;
  severity: string;
  status: string;
  error: string | null;
}

/* ---------- Constants ---------- */

const TIMEZONES = [
  'UTC',
  'Europe/Zurich', 'Europe/Berlin', 'Europe/Vienna', 'Europe/London',
  'Europe/Paris', 'Europe/Rome', 'Europe/Madrid', 'Europe/Amsterdam',
  'Europe/Brussels', 'Europe/Stockholm', 'Europe/Oslo', 'Europe/Helsinki',
  'Europe/Warsaw', 'Europe/Prague', 'Europe/Budapest', 'Europe/Bucharest',
  'Europe/Athens', 'Europe/Istanbul', 'Europe/Moscow',
  'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific', 'US/Alaska', 'US/Hawaii',
  'Canada/Eastern', 'Canada/Central', 'Canada/Pacific',
  'America/Sao_Paulo', 'America/Buenos_Aires', 'America/Mexico_City',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore',
  'Asia/Seoul', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Bangkok',
  'Australia/Sydney', 'Australia/Melbourne', 'Australia/Perth',
  'Pacific/Auckland', 'Africa/Cairo', 'Africa/Johannesburg',
];

const ACCENT_COLORS = [
  { name: 'Sky', value: '#0ea5e9' },
  { name: 'Violet', value: '#8b5cf6' },
  { name: 'Emerald', value: '#10b981' },
  { name: 'Rose', value: '#f43f5e' },
  { name: 'Amber', value: '#f59e0b' },
  { name: 'Cyan', value: '#06b6d4' },
];

const TAB_ICONS: Record<Tab, typeof Settings> = {
  system: Settings,
  monitoring: Activity,
  notifications: Bell,
  appearance: Palette,
  api: Key,
};

const inputCls =
  'w-full max-w-sm px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 transition-colors';

const inputSmCls =
  'w-40 px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 transition-colors';

/* ---------- Helpers ---------- */

function loadPref<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const v = localStorage.getItem(`ng_pref_${key}`);
    return v !== null ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

function savePref(key: string, value: unknown) {
  if (typeof window === 'undefined') return;
  localStorage.setItem(`ng_pref_${key}`, JSON.stringify(value));
}

/* ---------- Component ---------- */

export default function SettingsPage() {
  const toast = useToastStore();
  const qc = useQueryClient();

  const [activeTab, setActiveTab] = useState<Tab>('system');

  /* ---- System + Monitoring state ---- */
  const [siteName, setSiteName] = useState('');
  const [timezone, setTimezone] = useState('');
  const [pingInterval, setPingInterval] = useState('');
  const [latencyThreshold, setLatencyThreshold] = useState('');
  const [proxmoxInterval, setProxmoxInterval] = useState('');

  /* ---- Notifications state ---- */
  const [notifyEnabled, setNotifyEnabled] = useState(false);
  const [telegramToken, setTelegramToken] = useState('');
  const [telegramChat, setTelegramChat] = useState('');
  const [discordWebhook, setDiscordWebhook] = useState('');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [webhookSecret, setWebhookSecret] = useState('');
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState('');
  const [smtpUser, setSmtpUser] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [smtpFrom, setSmtpFrom] = useState('');
  const [smtpTo, setSmtpTo] = useState('');
  const [testingChannel, setTestingChannel] = useState<string | null>(null);

  /* ---- Appearance state ---- */
  const [accentColor, setAccentColor] = useState(() => loadPref('accentColor', '#0ea5e9'));
  const [sidebarPosition, setSidebarPosition] = useState<'left' | 'right'>(() => loadPref('sidebarPosition', 'left'));
  const [density, setDensity] = useState<'comfortable' | 'compact'>(() => loadPref('density', 'comfortable'));
  const [fontSize, setFontSize] = useState<'sm' | 'base' | 'lg'>(() => loadPref('fontSize', 'sm'));

  /* ---- API keys state ---- */
  const [createKeyModal, setCreateKeyModal] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyRole, setNewKeyRole] = useState<'readonly' | 'editor' | 'admin'>('readonly');
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  /* ---- Queries ---- */

  const { data: settings, isLoading: settingsLoading } = useQuery<SettingsData>({
    queryKey: ['settings'],
    queryFn: () => get('/settings/json'),
  });

  const populateFromSettings = useCallback((s: SettingsData) => {
    setSiteName(s.site_name);
    setTimezone(s.timezone);
    setPingInterval(s.ping_interval);
    setLatencyThreshold(s.latency_threshold_ms);
    setProxmoxInterval(s.proxmox_interval);
    setNotifyEnabled(s.notify_enabled === '1');
    setTelegramToken(s.telegram_bot_token);
    setTelegramChat(s.telegram_chat_id);
    setDiscordWebhook(s.discord_webhook_url);
    setWebhookUrl(s.webhook_url);
    setWebhookSecret(s.webhook_secret);
    setSmtpHost(s.smtp_host);
    setSmtpPort(s.smtp_port);
    setSmtpUser(s.smtp_user);
    setSmtpFrom(s.smtp_from);
    setSmtpTo(s.smtp_to);
    setSmtpPassword('');
  }, []);

  useEffect(() => {
    if (settings) populateFromSettings(settings);
  }, [settings, populateFromSettings]);

  const { data: apiKeys, isLoading: keysLoading } = useQuery<ApiKeyEntry[]>({
    queryKey: ['api-keys'],
    queryFn: () => get('/settings/api-keys'),
    enabled: activeTab === 'api',
  });

  const { data: notifHistory } = useQuery<NotifLog[]>({
    queryKey: ['notification-history'],
    queryFn: () => get('/settings/notifications/history'),
    enabled: activeTab === 'notifications',
  });

  /* ---- Mutations ---- */

  const saveSettingsMut = useMutation({
    mutationFn: (params: URLSearchParams) =>
      api('/settings/save', { method: 'POST', body: params }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('Settings saved', 'success');
    },
    onError: () => toast.show('Failed to save settings', 'error'),
  });

  const saveNotifMut = useMutation({
    mutationFn: (params: URLSearchParams) =>
      api('/settings/notifications/save', { method: 'POST', body: params }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('Notification settings saved', 'success');
    },
    onError: () => toast.show('Failed to save notification settings', 'error'),
  });

  const testNotifMut = useMutation({
    mutationFn: (channel: string) =>
      post<{ ok: boolean; message: string }>('/settings/notifications/test', { channel }),
    onSuccess: (data) => {
      toast.show(data.message || 'Test sent', 'success');
      setTestingChannel(null);
      qc.invalidateQueries({ queryKey: ['notification-history'] });
    },
    onError: () => {
      toast.show('Test notification failed', 'error');
      setTestingChannel(null);
    },
  });

  const createKeyMut = useMutation({
    mutationFn: (body: { name: string; role: string }) =>
      post<{ ok: boolean; key: string; id: number; prefix: string }>('/settings/api-keys/create', body),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      toast.show('API key created', 'success');
    },
    onError: () => toast.show('Failed to create API key', 'error'),
  });

  const deleteKeyMut = useMutation({
    mutationFn: (id: number) => del(`/settings/api-keys/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      toast.show('API key deleted', 'success');
    },
    onError: () => toast.show('Failed to delete API key', 'error'),
  });

  /* ---- Handlers ---- */

  function handleSaveSystem() {
    const params = new URLSearchParams();
    params.set('site_name', siteName);
    params.set('timezone', timezone);
    // Keep existing monitoring values
    params.set('ping_interval', pingInterval);
    params.set('latency_threshold', latencyThreshold);
    params.set('proxmox_interval', proxmoxInterval);
    saveSettingsMut.mutate(params);
  }

  function handleSaveMonitoring() {
    const params = new URLSearchParams();
    // Keep existing system values
    params.set('site_name', siteName);
    params.set('timezone', timezone);
    params.set('ping_interval', pingInterval);
    params.set('latency_threshold', latencyThreshold);
    params.set('proxmox_interval', proxmoxInterval);
    saveSettingsMut.mutate(params);
  }

  function handleSaveNotifications() {
    const params = new URLSearchParams();
    params.set('notify_enabled', notifyEnabled ? 'on' : '0');
    params.set('telegram_bot_token', telegramToken);
    params.set('telegram_chat_id', telegramChat);
    params.set('discord_webhook_url', discordWebhook);
    params.set('webhook_url', webhookUrl);
    params.set('webhook_secret', webhookSecret);
    params.set('smtp_host', smtpHost);
    params.set('smtp_port', smtpPort);
    params.set('smtp_user', smtpUser);
    params.set('smtp_password', smtpPassword);
    params.set('smtp_from', smtpFrom);
    params.set('smtp_to', smtpTo);
    saveNotifMut.mutate(params);
  }

  function handleTestChannel(channel: string) {
    setTestingChannel(channel);
    testNotifMut.mutate(channel);
  }

  function handleSaveAppearance() {
    savePref('accentColor', accentColor);
    savePref('sidebarPosition', sidebarPosition);
    savePref('density', density);
    savePref('fontSize', fontSize);
    toast.show('Preferences saved to browser', 'success');
  }

  function handleCreateKey(e: React.FormEvent) {
    e.preventDefault();
    if (!newKeyName.trim()) {
      toast.show('Name is required', 'warning');
      return;
    }
    createKeyMut.mutate({ name: newKeyName, role: newKeyRole });
  }

  function handleDeleteKey(k: ApiKeyEntry) {
    if (window.confirm(`Delete API key "${k.name}"?`)) {
      deleteKeyMut.mutate(k.id);
    }
  }

  function handleCopyKey() {
    if (createdKey) {
      navigator.clipboard.writeText(createdKey);
      toast.show('Copied to clipboard', 'success');
    }
  }

  function closeCreateModal() {
    setCreateKeyModal(false);
    setCreatedKey(null);
    setNewKeyName('');
    setNewKeyRole('readonly');
  }

  /* ---- Tab definitions ---- */

  const tabs: { key: Tab; label: string }[] = [
    { key: 'system', label: 'System' },
    { key: 'monitoring', label: 'Monitoring' },
    { key: 'notifications', label: 'Notifications' },
    { key: 'appearance', label: 'Appearance' },
    { key: 'api', label: 'API' },
  ];

  const isSaving = saveSettingsMut.isPending || saveNotifMut.isPending;

  /* ---- Render ---- */

  return (
    <div>
      <PageHeader title="Settings" description="Configure Nodeglow" />

      <GlassCard className="p-3 mb-6 border-amber-500/30 bg-amber-500/5">
        <div className="flex items-center gap-2">
          <Badge variant="severity" severity="warning">Admin</Badge>
          <p className="text-xs text-amber-300">Some settings require admin privileges to modify.</p>
        </div>
      </GlassCard>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => {
          const Icon = TAB_ICONS[tab.key];
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab.key
                  ? 'text-sky-400 border-b-2 border-sky-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* ==================== SYSTEM TAB ==================== */}
      {activeTab === 'system' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">General</h3>
            {settingsLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-8 w-80" />
                <Skeleton className="h-8 w-80" />
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Instance Name</label>
                  <input
                    type="text"
                    value={siteName}
                    onChange={(e) => setSiteName(e.target.value)}
                    className={inputCls}
                    placeholder="Nodeglow"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Timezone</label>
                  <select
                    value={timezone}
                    onChange={(e) => setTimezone(e.target.value)}
                    className="w-full max-w-sm px-3 py-1.5 rounded-md bg-[#111621] border border-white/[0.06] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50 transition-colors [&>option]:bg-[#111621] [&>option]:text-slate-200"
                  >
                    {TIMEZONES.map((tz) => (
                      <option key={tz} value={tz}>{tz}</option>
                    ))}
                    {/* Show current value even if not in list */}
                    {timezone && !TIMEZONES.includes(timezone) && (
                      <option value={timezone}>{timezone}</option>
                    )}
                  </select>
                </div>
              </div>
            )}
          </GlassCard>
          <div className="flex justify-end">
            <Button size="sm" onClick={handleSaveSystem} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        </div>
      )}

      {/* ==================== MONITORING TAB ==================== */}
      {activeTab === 'monitoring' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Ping Settings</h3>
            {settingsLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-8 w-40" />
                <Skeleton className="h-8 w-40" />
                <Skeleton className="h-8 w-40" />
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Check Interval (seconds)</label>
                  <input
                    type="number"
                    value={pingInterval}
                    onChange={(e) => setPingInterval(e.target.value)}
                    className={inputSmCls}
                    min={10}
                    max={3600}
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Timeout (ms)</label>
                  <input
                    type="number"
                    value={latencyThreshold}
                    onChange={(e) => setLatencyThreshold(e.target.value)}
                    className={inputSmCls}
                    placeholder="e.g. 5000"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Integration Interval (seconds)</label>
                  <input
                    type="number"
                    value={proxmoxInterval}
                    onChange={(e) => setProxmoxInterval(e.target.value)}
                    className={inputSmCls}
                    min={10}
                    max={3600}
                  />
                </div>
              </div>
            )}
          </GlassCard>
          <div className="flex justify-end">
            <Button size="sm" onClick={handleSaveMonitoring} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        </div>
      )}

      {/* ==================== NOTIFICATIONS TAB ==================== */}
      {activeTab === 'notifications' && (
        <div className="space-y-4">
          {/* Enable toggle */}
          <GlassCard className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-slate-300">Enable Notifications</h3>
                <p className="text-xs text-slate-500 mt-0.5">Send alerts when incidents are created or resolved.</p>
              </div>
              <button
                onClick={() => setNotifyEnabled(!notifyEnabled)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  notifyEnabled ? 'bg-sky-500' : 'bg-white/[0.1]'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                    notifyEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          </GlassCard>

          {/* Telegram */}
          <GlassCard className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-slate-300">Telegram</h3>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => handleTestChannel('telegram')}
                disabled={testingChannel === 'telegram'}
              >
                <Send size={12} />
                {testingChannel === 'telegram' ? 'Sending...' : 'Test'}
              </Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Bot Token</label>
                <input
                  type="password"
                  value={telegramToken}
                  onChange={(e) => setTelegramToken(e.target.value)}
                  className={inputCls}
                  placeholder="123456:ABC-DEF..."
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Chat ID</label>
                <input
                  type="text"
                  value={telegramChat}
                  onChange={(e) => setTelegramChat(e.target.value)}
                  className={inputCls}
                  placeholder="-1001234567890"
                />
              </div>
            </div>
          </GlassCard>

          {/* Discord */}
          <GlassCard className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-slate-300">Discord</h3>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => handleTestChannel('discord')}
                disabled={testingChannel === 'discord'}
              >
                <Send size={12} />
                {testingChannel === 'discord' ? 'Sending...' : 'Test'}
              </Button>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Webhook URL</label>
              <input
                type="text"
                value={discordWebhook}
                onChange={(e) => setDiscordWebhook(e.target.value)}
                className={inputCls}
                placeholder="https://discord.com/api/webhooks/..."
              />
            </div>
          </GlassCard>

          {/* Webhook */}
          <GlassCard className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-slate-300">Webhook</h3>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => handleTestChannel('webhook')}
                disabled={testingChannel === 'webhook'}
              >
                <Send size={12} />
                {testingChannel === 'webhook' ? 'Sending...' : 'Test'}
              </Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">URL</label>
                <input
                  type="text"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  className={inputCls}
                  placeholder="https://example.com/webhook"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Secret</label>
                <input
                  type="password"
                  value={webhookSecret}
                  onChange={(e) => setWebhookSecret(e.target.value)}
                  className={inputCls}
                  placeholder="Optional signing secret"
                />
              </div>
            </div>
          </GlassCard>

          {/* Email / SMTP */}
          <GlassCard className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-slate-300">Email / SMTP</h3>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => handleTestChannel('email')}
                disabled={testingChannel === 'email'}
              >
                <Send size={12} />
                {testingChannel === 'email' ? 'Sending...' : 'Test'}
              </Button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-slate-500 mb-1">SMTP Host</label>
                <input
                  type="text"
                  value={smtpHost}
                  onChange={(e) => setSmtpHost(e.target.value)}
                  className={inputCls}
                  placeholder="smtp.example.com"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Port</label>
                <input
                  type="number"
                  value={smtpPort}
                  onChange={(e) => setSmtpPort(e.target.value)}
                  className={inputSmCls}
                  placeholder="587"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Username</label>
                <input
                  type="text"
                  value={smtpUser}
                  onChange={(e) => setSmtpUser(e.target.value)}
                  className={inputCls}
                  placeholder="user@example.com"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">
                  Password {settings?.smtp_has_pw && <span className="text-emerald-400 ml-1">(set)</span>}
                </label>
                <input
                  type="password"
                  value={smtpPassword}
                  onChange={(e) => setSmtpPassword(e.target.value)}
                  className={inputCls}
                  placeholder={settings?.smtp_has_pw ? 'Leave blank to keep' : 'Password'}
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">From Address</label>
                <input
                  type="text"
                  value={smtpFrom}
                  onChange={(e) => setSmtpFrom(e.target.value)}
                  className={inputCls}
                  placeholder="nodeglow@example.com"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">To Address</label>
                <input
                  type="text"
                  value={smtpTo}
                  onChange={(e) => setSmtpTo(e.target.value)}
                  className={inputCls}
                  placeholder="admin@example.com"
                />
              </div>
            </div>
          </GlassCard>

          <div className="flex justify-end">
            <Button size="sm" onClick={handleSaveNotifications} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save Notification Settings'}
            </Button>
          </div>

          {/* Notification History */}
          {notifHistory && notifHistory.length > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">Recent Notifications</h3>
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {notifHistory.map((n) => (
                  <div
                    key={n.id}
                    className="flex items-center gap-3 text-xs py-1.5 border-b border-white/[0.04] last:border-0"
                  >
                    {n.status === 'sent' ? (
                      <CheckCircle size={14} className="text-emerald-400 shrink-0" />
                    ) : (
                      <XCircle size={14} className="text-red-400 shrink-0" />
                    )}
                    <Badge variant="severity" severity={n.status === 'sent' ? 'info' : 'critical'}>
                      {n.channel}
                    </Badge>
                    <span className="text-slate-300 truncate">{n.title}</span>
                    {n.error && (
                      <span className="text-red-400 truncate ml-auto flex items-center gap-1">
                        <AlertTriangle size={12} />
                        {n.error}
                      </span>
                    )}
                    {n.timestamp && (
                      <span className="text-slate-500 shrink-0 ml-auto flex items-center gap-1">
                        <Clock size={12} />
                        {new Date(n.timestamp).toLocaleString()}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </GlassCard>
          )}
        </div>
      )}

      {/* ==================== APPEARANCE TAB ==================== */}
      {activeTab === 'appearance' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Accent Color</h3>
            <div className="flex gap-3">
              {ACCENT_COLORS.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setAccentColor(c.value)}
                  className={`w-8 h-8 rounded-full border-2 transition-all ${
                    accentColor === c.value ? 'border-white scale-110' : 'border-transparent'
                  }`}
                  style={{ backgroundColor: c.value }}
                  title={c.name}
                />
              ))}
            </div>
          </GlassCard>

          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Sidebar Position</h3>
            <div className="flex gap-2">
              {(['left', 'right'] as const).map((pos) => (
                <button
                  key={pos}
                  onClick={() => setSidebarPosition(pos)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    sidebarPosition === pos
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {pos.charAt(0).toUpperCase() + pos.slice(1)}
                </button>
              ))}
            </div>
          </GlassCard>

          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Density</h3>
            <div className="flex gap-2">
              {(['comfortable', 'compact'] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setDensity(d)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    density === d
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {d.charAt(0).toUpperCase() + d.slice(1)}
                </button>
              ))}
            </div>
          </GlassCard>

          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Font Size</h3>
            <div className="flex gap-2">
              {([
                { key: 'sm' as const, label: 'Small' },
                { key: 'base' as const, label: 'Default' },
                { key: 'lg' as const, label: 'Large' },
              ]).map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFontSize(f.key)}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    fontSize === f.key
                      ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </GlassCard>

          <div className="flex justify-end">
            <Button size="sm" onClick={handleSaveAppearance}>Save Preferences</Button>
          </div>
        </div>
      )}

      {/* ==================== API TAB ==================== */}
      {activeTab === 'api' && (
        <div className="space-y-4">
          <GlassCard className="p-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-medium text-slate-300">API Keys</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  Manage API keys for programmatic access. Keys use the <code className="text-sky-400">ng_</code> prefix.
                </p>
              </div>
              <Button size="sm" onClick={() => setCreateKeyModal(true)}>
                <Plus size={14} />
                Create Key
              </Button>
            </div>

            {keysLoading ? (
              <div className="space-y-3">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : !apiKeys?.length ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-500">
                <Key size={36} className="mb-3 text-slate-600" />
                <p className="text-sm">No API keys created yet.</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/[0.06] text-left text-xs text-slate-400 uppercase tracking-wider">
                    <th className="py-2 font-medium">Name</th>
                    <th className="py-2 font-medium">Prefix</th>
                    <th className="py-2 font-medium">Role</th>
                    <th className="py-2 font-medium hidden sm:table-cell">Created</th>
                    <th className="py-2 font-medium hidden sm:table-cell">Last Used</th>
                    <th className="py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {apiKeys.map((k) => (
                    <tr key={k.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="py-2.5 text-slate-200 font-medium">{k.name}</td>
                      <td className="py-2.5">
                        <code className="text-xs text-sky-400 bg-sky-500/10 px-1.5 py-0.5 rounded">
                          {k.prefix}...
                        </code>
                      </td>
                      <td className="py-2.5">
                        <Badge variant="severity" severity={k.role === 'admin' ? 'critical' : k.role === 'editor' ? 'warning' : 'info'}>
                          {k.role}
                        </Badge>
                      </td>
                      <td className="py-2.5 text-slate-400 text-xs hidden sm:table-cell">
                        {k.created_at ? new Date(k.created_at).toLocaleDateString() : '--'}
                      </td>
                      <td className="py-2.5 text-slate-400 text-xs hidden sm:table-cell">
                        {k.last_used ? new Date(k.last_used).toLocaleDateString() : 'Never'}
                      </td>
                      <td className="py-2.5 text-right">
                        <button
                          onClick={() => handleDeleteKey(k)}
                          className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </GlassCard>
        </div>
      )}

      {/* ==================== CREATE API KEY MODAL ==================== */}
      <Modal open={createKeyModal} onClose={closeCreateModal} title={createdKey ? 'API Key Created' : 'Create API Key'}>
        {createdKey ? (
          <div className="space-y-4">
            <p className="text-sm text-slate-300">
              Your new API key is shown below. Copy it now -- it will not be shown again.
            </p>
            <div className="flex items-center gap-2 p-3 rounded-md bg-white/[0.04] border border-white/[0.06]">
              <code className="text-sm text-emerald-400 font-mono break-all flex-1">{createdKey}</code>
              <button
                onClick={handleCopyKey}
                className="p-1.5 rounded-md text-slate-400 hover:text-sky-400 hover:bg-sky-500/10 transition-colors shrink-0"
                title="Copy"
              >
                <Copy size={16} />
              </button>
            </div>
            <div className="flex justify-end">
              <Button size="sm" onClick={closeCreateModal}>Done</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreateKey} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Key Name</label>
              <input
                className={inputCls}
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                placeholder="e.g. Grafana readonly"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Role</label>
              <select
                className="w-full max-w-sm rounded-md border border-white/[0.08] bg-[#111621] px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50 transition-colors"
                value={newKeyRole}
                onChange={(e) => setNewKeyRole(e.target.value as 'readonly' | 'editor' | 'admin')}
              >
                <option value="readonly" className="bg-[#111621] text-slate-200">Read-only</option>
                <option value="editor" className="bg-[#111621] text-slate-200">Editor</option>
                <option value="admin" className="bg-[#111621] text-slate-200">Admin</option>
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" size="sm" onClick={closeCreateModal}>Cancel</Button>
              <Button type="submit" size="sm" disabled={createKeyMut.isPending}>
                {createKeyMut.isPending ? 'Creating...' : 'Create'}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
