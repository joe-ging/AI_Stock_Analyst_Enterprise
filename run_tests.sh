#!/bin/bash
set -e

echo "=== Running Unit Tests inside Docker engine container ==="
docker-compose exec -T engine pytest test_main.py -v
echo "=== All Tests Passed! ==="
