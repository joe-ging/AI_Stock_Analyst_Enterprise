#!/bin/bash
# AI Stock Analyst Enterprise — Automated Safe Deployment & Proxy Recovery
set -e

echo "=== 1. Starting Proxy Diagnosis & Recovery ==="

# Check if SSH tunnel is running on local 1080
if ! ss -tlnp 2>/dev/null | grep -q "127.0.0.1:1080"; then
    echo "SSH tunnel down. Re-establishing connection to Sydney (3.27.11.100)..."
    ssh -o StrictHostKeyChecking=no -D 127.0.0.1:1080 -f -N -i ~/.ssh/canonical_key.pem ubuntu@3.27.11.100
    echo "SSH tunnel started."
else
    echo "SSH tunnel is running."
fi

# Check if socat redirector is running on 1088
if ! ss -tlnp 2>/dev/null | grep -q "0.0.0.0:1088"; then
    echo "Socat gateway down. Re-starting socat on port 1088..."
    nohup socat TCP-LISTEN:1088,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:1080 &>/dev/null &
    echo "Socat gateway started."
else
    echo "Socat gateway is running."
fi

echo "=== 2. Re-building Application Containers ==="
cd ~/AI_Stock_Analyst_Enterprise

# Pull latest code
git pull origin main

# Stop and recreate ONLY application services (protecting Milvus/DB/Cache)
docker-compose stop engine gateway worker
docker-compose rm -f engine gateway worker
docker-compose up -d --build engine gateway worker

echo "=== 3. Deployment Health Check ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'enterprise|milvus'
