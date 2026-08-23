-- Extend a lease, but only for the device that actually holds it.
-- KEYS[1] = leases zset
-- ARGV = shard_id, device_id, now_ms, lease_ttl_ms
-- returns the new deadline_ms, or 0 if the caller no longer owns this shard.

local sid, device, now, ttl = ARGV[1], ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4])
local hkey = 'nm:shard:' .. sid

if redis.call('HGET', hkey, 'state') ~= 'claimed' then return 0 end
if redis.call('HGET', hkey, 'device_id') ~= device then return 0 end

local deadline = now + ttl
redis.call('HSET', hkey, 'deadline', deadline)
redis.call('ZADD', KEYS[1], deadline, sid)
return deadline
