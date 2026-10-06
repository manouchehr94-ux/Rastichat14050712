#!/bin/bash
# One-time, idempotent preparation of the SANDBOX staging state used by run-matrix.sh (see README.md).
#
# Everything is local and synthetic: a throw-away CA and certificates for *.example.test (mapped to 127.0.0.1 in /etc/hosts),
# its own PostgreSQL database/role, Redis db 5, nginx sites rendered from deploy/nginx by the repository's own installer, the two
# Next dashboards built in standalone mode, and a seeded synthetic dataset. No remote host is contacted, nothing is production.
#
# usage (root):  SANDBOX_DIR=/tmp/sbx VENV=/tmp/venv-rc bash scripts/staging/sandbox/setup.sh
# needs on the box: nginx, postgresql (server on 127.0.0.1:5432, `su postgres` works), redis, node/npm, openssl, the python venv.
set -euo pipefail
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR}; R=$(cd "$SB/../../.." && pwd)
PY=${VENV:-/tmp/venv}/bin/python
[ "$(id -u)" -eq 0 ] || { echo "run as root (writes /etc/nginx, /etc/hosts, /etc/letsencrypt of THIS sandbox)"; exit 1; }
for c in nginx openssl node npm redis-cli psql; do command -v $c >/dev/null || { echo "missing: $c"; exit 1; }; done
BACK=chat-stg.example.test; OPER=operator-stg.example.test; PLAT=platform-stg.example.test
ST=$S/stg; L=$ST/logs; mkdir -p $ST $L /srv/stg/media /srv/stg/static /srv/stg/widgetroot $S/evidence
umask 022

echo "== hosts"
# The names resolve to 192.0.2.2 (RFC 5737 documentation range, added as an alias on lo), NOT to 127.0.0.1: recent Chromium blocks
# requests from a "public" page (the fake embedding origins) to a loopback address (local-network access), which would hang every
# browser test that embeds the widget from a fake origin. A non-loopback address behaves like a real public host.
ip addr show lo | grep -q "192.0.2.2/" || ip addr add 192.0.2.2/32 dev lo
for h in $BACK $OPER $PLAT embed-allowed.example.test embed-forbidden.example.test; do
  sed -i -E "/[[:space:]]$h(\$|[[:space:]])/d" /etc/hosts
  echo "192.0.2.2 $h" >> /etc/hosts
done

echo "== local CA + certificates (self-contained sandbox PKI)"
if [ ! -f $ST/ca.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout $ST/ca.key -out $ST/ca.crt -days 30 -subj "/CN=RastiChat sandbox CA" 2>/dev/null
  for d in $BACK $OPER $PLAT; do
    live=/etc/letsencrypt/live/$d; mkdir -p $live
    openssl req -newkey rsa:2048 -nodes -keyout $live/privkey.pem -out /tmp/$d.csr -subj "/CN=$d" 2>/dev/null
    printf "subjectAltName=DNS:$d\n" > /tmp/$d.ext
    openssl x509 -req -in /tmp/$d.csr -CA $ST/ca.crt -CAkey $ST/ca.key -CAcreateserial -out $live/fullchain.pem -days 30 -extfile /tmp/$d.ext 2>/dev/null
    cp $ST/ca.crt $live/chain.pem
  done
fi
cp $ST/ca.crt /usr/local/share/ca-certificates/rastichat-sandbox-ca.crt 2>/dev/null && update-ca-certificates >/dev/null 2>&1 || true
# Chromium (Playwright) trusts the sandbox CA through the NSS database (the specs run with ignoreHTTPSErrors:false)
if command -v certutil >/dev/null; then
  mkdir -p $HOME/.pki/nssdb; [ -f $HOME/.pki/nssdb/cert9.db ] || certutil -d sql:$HOME/.pki/nssdb -N --empty-password
  certutil -d sql:$HOME/.pki/nssdb -D -n rastichat-sandbox-ca >/dev/null 2>&1 || true
  certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n rastichat-sandbox-ca -i $ST/ca.crt
else echo "WARNING: certutil (libnss3-tools) missing: Chromium will not trust the sandbox CA"; fi

echo "== PostgreSQL role + database, Redis"
su postgres -c "psql -qAt -c \"select 1 from pg_roles where rolname='rasti'\"" | grep -q 1 || su postgres -c "psql -qc \"create role rasti login superuser password 'rasti'\""
su postgres -c "psql -qAt -c \"select 1 from pg_database where datname='rasti_stg'\"" | grep -q 1 || su postgres -c "createdb -O rasti rasti_stg"
redis-cli -n 5 flushdb >/dev/null

echo "== env files"
SECRET=$($PY -c 'import secrets;print(secrets.token_urlsafe(48))')
SEEDPW='Stg-Only-Pass-123'
[ -f $ST/backend.env ] || cat > $ST/backend.env <<EOF
export ENVIRONMENT=staging DEBUG=0
export DJANGO_SECRET_KEY=$SECRET
export DJANGO_SETTINGS_MODULE=config.settings
export ALLOWED_HOSTS=$BACK
export CSRF_TRUSTED_ORIGINS=https://$OPER,https://$PLAT
export CORS_ALLOWED_ORIGINS=https://$OPER,https://$PLAT,https://$BACK
export DATABASE_URL=postgres://rasti:rasti@127.0.0.1:5432/rasti_stg
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=rasti_stg DB_USER=rasti DB_PASSWORD=rasti
export REDIS_URL=redis://127.0.0.1:6379/5 REDIS_HOST=127.0.0.1
export MEDIA_ROOT=/srv/stg/media STATIC_ROOT=/srv/stg/static
export MONITORING_TOKEN=stg-only-monitoring-token-$(openssl rand -hex 8)
export WIDGET_REQUIRE_ALLOWED_DOMAINS=1
export SECURE_HSTS_SECONDS=3600 TIME_ZONE=UTC DJANGO_LOG_LEVEL=INFO
export LOGIN_THROTTLE_RATE=10/min WIDGET_START_THROTTLE_RATE=20/min
export STAGING_SEED_PASSWORD='$SEEDPW'
EOF
cat > $ST/env.staging <<EOF
BACKEND_DOMAIN=$BACK
OPERATOR_DOMAIN=$OPER
PLATFORM_DOMAIN=$PLAT
BACKEND_PORT=8100
OPERATOR_PORT=3100
PLATFORM_PORT=3101
WIDGET_PORT=8180
MEDIA_HOST_PATH=/srv/stg/media
STATIC_HOST_PATH=/srv/stg/static
EOF

echo "== database: migrate, collectstatic, seed"
( . $ST/backend.env; cd $R/backend
  $PY manage.py migrate --noinput -v0
  $PY manage.py collectstatic --noinput -v0 >/dev/null
  SEED_OUT=$ST/keys.json $PY manage.py shell < $SB/seed.py | tail -1 )

echo "== integrations used by the integration-platform specs (a legitimate host and an unrelated one)"
( . $ST/backend.env; cd $R/backend
  PLATID=$($PY manage.py shell -c "from platforms.models import Platform; print(Platform.objects.get(name='STG Platform').id)" 2>/dev/null | tail -1)
  OUT=$ST/integration.json; echo '{}' > $OUT
  for slug in stg-host stg-mallory; do
    if ! $PY manage.py shell -c "from integrations.models import Integration; import sys; sys.exit(0 if Integration.objects.filter(slug='$slug').exists() else 1)" >/dev/null 2>&1; then
      $PY manage.py integration_create --slug $slug --name "$slug" --platform-id $PLATID \
        --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,conversations:initiate,context:write >/dev/null
      $PY manage.py integration_keygen --out-dir $ST --name $slug >/dev/null
      KID=$($PY manage.py integration_key_add --integration $slug --public-key-file $ST/$slug.public.pem | sed -n 's/^kid=//p')
      echo "$KID" > $ST/$slug.kid
    fi
  done
  python3 - <<PY
import json
d = {s: {"slug": s, "kid": open("$ST/%s.kid" % s).read().strip(), "private_key_file": "$ST/%s.private.pem" % s} for s in ("stg-host", "stg-mallory")}
json.dump(d, open("$OUT", "w"))
PY
)

echo "== dashboards (Next standalone, API/WS URLs baked in) + widget"
for app in operator platform; do
  dir=$R/apps/$app-dashboard; out=$ST/$app-app
  ( cd $dir && NEXT_PUBLIC_API_BASE_URL=https://$BACK/api/v1 NEXT_PUBLIC_WS_BASE_URL=wss://$BACK/ws npm run build >/dev/null 2>&1 )
  rm -rf $out; cp -r $dir/.next/standalone $out; mkdir -p $out/.next; cp -r $dir/.next/static $out/.next/static; [ -d $dir/public ] && cp -r $dir/public $out/public
  [ -f $out/server.js ] || { echo "standalone server.js not found for $app"; exit 1; }
done
( cd $R/packages/widget && npm run build >/dev/null && cp dist/widget.iife.js /srv/stg/widgetroot/widget.js )
( cd $R && git checkout -q -- apps/operator-dashboard/AGENTS.md apps/platform-dashboard/AGENTS.md 2>/dev/null || true )

echo "== nginx (sites rendered by the repository's own installer)"
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled /var/log/nginx
grep -q "sites-enabled" /etc/nginx/nginx.conf || sed -i 's#include /etc/nginx/conf.d/\*.conf;#include /etc/nginx/conf.d/*.conf;\n\tinclude /etc/nginx/sites-enabled/*;#' /etc/nginx/nginx.conf
grep -q "conf.d/\*.conf" /etc/nginx/nginx.conf || { echo "nginx.conf does not include conf.d"; exit 1; }
rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf
( cd $R && bash scripts/nginx/install-sites.sh $ST/env.staging </dev/null >/dev/null 2>&1 || true )
sed -i '/listen \[::\]/d' /etc/nginx/sites-available/rastichat-*.conf
chown -R www-data:www-data /srv/stg 2>/dev/null || true; chmod -R a+rX /srv/stg
nginx -t
echo "setup complete: $ST  (keys: $(cat $ST/keys.json))"
