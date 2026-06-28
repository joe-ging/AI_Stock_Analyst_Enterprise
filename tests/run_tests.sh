#!/bin/bash
set -e

echo "=== Checking if Engine Container is Running ==="
for i in {1..30}; do
  if docker ps | grep enterprise-rag-engine | grep -iq "healthy\|up"; then
    echo "Engine container is ready!"
    break
  fi
  echo "Waiting for engine container to start..."
  sleep 2
done

echo "=== Running Unit Tests inside Docker engine container ==="
docker-compose exec -T engine pytest test_main.py -v
echo "=== All Tests Passed! ==="
