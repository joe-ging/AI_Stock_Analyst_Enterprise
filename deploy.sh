#!/bin/bash
# AI Stock Analyst Enterprise — Automated Safe Deployment & Proxy Recovery
set -e

echo "=== 1. Re-building Application Containers ==="
cd ~/AI_Stock_Analyst_Enterprise

# Pull latest code
git pull origin main

# Stop and recreate ONLY application services (protecting Milvus/DB/Cache)
docker-compose stop engine gateway worker
docker-compose rm -f engine gateway worker
docker-compose up -d --build engine gateway worker

echo "=== 3. Deployment Health Check ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'enterprise|milvus'
