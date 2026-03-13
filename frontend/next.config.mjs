/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',

  // In Docker: proxy to backend container. In dev: set BACKEND_URL=http://10.10.30.52:8000
  async rewrites() {
    const backend = process.env.BACKEND_URL || 'http://nodeglow:8000';
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/hosts/api/:path*', destination: `${backend}/hosts/api/:path*` },
      { source: '/syslog/api/:path*', destination: `${backend}/syslog/api/:path*` },
      { source: '/syslog/stream', destination: `${backend}/syslog/stream` },
      { source: '/syslog/templates', destination: `${backend}/syslog/templates` },
      { source: '/subnet-scanner', destination: `${backend}/subnet-scanner` },
      { source: '/snmp', destination: `${backend}/snmp` },
      { source: '/credentials', destination: `${backend}/credentials` },
      { source: '/login', destination: `${backend}/login` },
      { source: '/logout', destination: `${backend}/logout` },
      { source: '/health', destination: `${backend}/health` },
      { source: '/ws/:path*', destination: `${backend}/ws/:path*` },
    ];
  },
};

export default nextConfig;
