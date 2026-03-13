/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',

  // Proxy API + data routes to backend. In dev: set BACKEND_URL=http://10.10.30.52:8000
  async rewrites() {
    const backend = process.env.BACKEND_URL || 'http://nodeglow:8000';
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/hosts/api/:path*', destination: `${backend}/hosts/api/:path*` },
      { source: '/syslog/api/:path*', destination: `${backend}/syslog/api/:path*` },
      { source: '/syslog/stream', destination: `${backend}/syslog/stream` },
      { source: '/health', destination: `${backend}/health` },
      { source: '/system/status', destination: `${backend}/system/status` },
      { source: '/settings/:path*', destination: `${backend}/settings/:path*` },
      { source: '/rules/:path*', destination: `${backend}/rules/:path*` },
      { source: '/ws/:path*', destination: `${backend}/ws/:path*` },
      { source: '/setup', destination: `${backend}/setup` },
      { source: '/setup/:path*', destination: `${backend}/setup/:path*` },
      { source: '/install/:path*', destination: `${backend}/install/:path*` },
      { source: '/static/:path*', destination: `${backend}/static/:path*` },
    ];
  },
};

export default nextConfig;
