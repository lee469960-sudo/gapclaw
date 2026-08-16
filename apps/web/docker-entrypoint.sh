#!/bin/sh
set -e
echo "waiting for api:8000/health ..."
i=0
while [ "$i" -lt 60 ]; do
  if wget -q -O - http://api:8000/health >/dev/null 2>&1; then
    echo "api is ready"
    exec nginx -g 'daemon off;'
  fi
  i=$((i + 1))
  sleep 2
done
echo "api not ready after 120s" >&2
exit 1
