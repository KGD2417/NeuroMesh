-- Requeue every shard whose lease expired. This is the only thing standing
-- between a phone that dropped off mid-shard and a job that never finishes.
--
-- KEYS[1] = leases zset
-- ARGV = now_ms, max_attempts, limit
-- returns a flat list of 7 fields per reaped shard:
--   shard_id, job_id, idx, outcome, done, failed, shard_count
--   outcome: 1 requeued, 2 failed permanently

local now, max_attempts, limit = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, limit)
local out = {}

for _, sid in ipairs(expired) do
  redis.call('ZREM', KEYS[1], sid)
  local hkey = 'nm:shard:' .. sid
  if redis.call('HGET', hkey, 'state') == 'claimed' then
    local job_id = redis.call('HGET', hkey, 'job_id')
    local jkey = 'nm:job:' .. job_id
    local attempts = tonumber(redis.call('HGET', hkey, 'attempts') or 0)
    local idx = redis.call('HGET', hkey, 'idx')
    redis.call('HINCRBY', jkey, 'claimed', -1)
    redis.call('HDEL', hkey, 'deadline', 'device_id')

    local outcome
    if attempts < max_attempts then
      redis.call('HSET', hkey, 'state', 'queued')
      redis.call('LPUSH', 'nm:q:' .. redis.call('HGET', hkey, 'tier'), sid)
      outcome = 1
    else
      redis.call('HSET', hkey, 'state', 'failed')
      redis.call('HINCRBY', jkey, 'failed', 1)
      outcome = 2
    end

    out[#out + 1] = sid
    out[#out + 1] = job_id
    out[#out + 1] = idx
    out[#out + 1] = tostring(outcome)
    out[#out + 1] = redis.call('HGET', jkey, 'done') or '0'
    out[#out + 1] = redis.call('HGET', jkey, 'failed') or '0'
    out[#out + 1] = redis.call('HGET', jkey, 'shard_count') or '0'
  end
end

return out
