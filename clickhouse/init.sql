CREATE TABLE IF NOT EXISTS syslog_messages
(
    timestamp       DateTime64(3, 'UTC') NOT NULL,
    received_at     DateTime64(3, 'UTC') NOT NULL DEFAULT now64(),
    source_ip       LowCardinality(String) NOT NULL,
    hostname        LowCardinality(String) DEFAULT '',
    host_id         Nullable(Int32),
    facility        Nullable(Int8),
    severity        Int8 DEFAULT 6,
    app_name        LowCardinality(String) DEFAULT '',
    message         String NOT NULL,
    template_hash   LowCardinality(String) DEFAULT '',
    tags            String DEFAULT '',
    noise_score     Int8 DEFAULT 50,
    INDEX idx_message_bloom message TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 4,
    INDEX idx_hostname_bloom hostname TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (severity, source_ip, timestamp)
TTL
    timestamp + INTERVAL 1  DAY  WHERE severity = 7,
    timestamp + INTERVAL 3  DAY  WHERE severity = 6,
    timestamp + INTERVAL 7  DAY  WHERE severity = 5,
    timestamp + INTERVAL 30 DAY  WHERE severity = 4,
    timestamp + INTERVAL 90 DAY  WHERE severity IN (0, 1, 2, 3)
SETTINGS
    index_granularity = 8192,
    ttl_only_drop_parts = 1;
