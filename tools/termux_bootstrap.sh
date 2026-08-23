#!/data/data/com.termux/files/usr/bin/bash
# Run the NeuroMesh orchestrator on a phone, under Termux.
#
# The demo is "four phones and no laptop", and the orchestrator has to live
# somewhere. Docker cannot run on an unrooted Android phone, so this brings up
# Postgres, Redis and uvicorn natively instead of through docker-compose.
#
#   pkg install git
#   git clone <repo> && cd NeuroMesh
#   bash tools/termux_bootstrap.sh
#
# Then read the LAN address it prints and type it into the other three phones.
#
# NOTE: this path has not been run on hardware in this repo's history -- the
# server itself is verified under docker-compose. Do the Termux dry run the day
# before the demo, not the morning of, because `pip install asyncpg` compiles
# from source and that is the step most likely to want a fix.

set -euo pipefail

PREFIX_BIN="${PREFIX:-/data/data/com.termux/files/usr}"
PGDATA="$HOME/neuromesh-pg"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> packages"
pkg update -y
# python-cryptography and python-pip come as packages so pip does not have to
# build Rust; asyncpg still compiles, which is what clang is for.
pkg install -y python python-pip clang binutils postgresql redis openssl libffi

echo "==> python deps"
pip install --upgrade pip wheel
pip install "fastapi" "uvicorn[standard]" "sqlalchemy[asyncio]" "asyncpg" \
    "alembic" "redis" "pydantic[email]" "pyjwt" "cryptography"

echo "==> postgres"
if [ ! -d "$PGDATA" ]; then
    initdb "$PGDATA"
fi
pg_ctl -D "$PGDATA" -l "$HOME/neuromesh-pg.log" start || true
sleep 3
createuser -s neuromesh 2>/dev/null || true
psql -d postgres -c "ALTER USER neuromesh WITH PASSWORD 'neuromesh';" >/dev/null
createdb -O neuromesh neuromesh 2>/dev/null || true

echo "==> redis"
pgrep -f redis-server >/dev/null || redis-server --daemonize yes --appendonly yes

echo "==> migrations"
cd "$REPO/server"
export NEUROMESH_DATABASE_DSN="postgresql+asyncpg://neuromesh:neuromesh@127.0.0.1:5432/neuromesh"
export NEUROMESH_REDIS_URL="redis://127.0.0.1:6379/0"
python -m alembic upgrade head

LAN_IP="$(ip route get 1 2>/dev/null | awk '{print $7; exit}')"
echo
echo "=============================================================="
echo " orchestrator:  http://${LAN_IP:-<phone-ip>}:8000"
echo " type that into the other three phones at the Setup screen"
echo "=============================================================="
echo

exec python -m uvicorn main:app --host 0.0.0.0 --port 8000
