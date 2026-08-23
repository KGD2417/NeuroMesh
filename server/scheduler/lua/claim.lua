-- Atomically claim the strongest shard this device is capable of running.
--
-- Never read-then-write: two devices claiming one shard is a double-credit bug,
-- so the pop, the state transition, the lease and the payload read all happen
-- inside this one script. LPOP is the point of serialisation.
--
-- KEYS[1] = leases zset
-- ARGV[1] = device_id
-- ARGV[2] = now_ms
-- ARGV[3] = lease_ttl_ms
-- ARGV[4] = max_attempts
-- ARGV[5..] = tiers to try, strongest first
-- returns {shard_id, job_id, index, model_ref, tier, deadline_ms, payload} or false

local device = ARGV[1]
local now = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local max_attempts = tonumber(ARGV[4])
local leases = KEYS[1]

for i = 5, #ARGV do
  local qkey = 'nm:q:' .. ARGV[i]
  while true do
    local sid = redis.call('LPOP', qkey)
    if not sid then break end

    local hkey = 'nm:shard:' .. sid
    local state = redis.call('HGET', hkey, 'state')

    -- Anything not queued is a stale queue entry (already done, failed, or
    -- picked up by a racing requeue). Drop it and keep walking.
    if state == 'queued' then
      local job_id = redis.call('HGET', hkey, 'job_id')
      local attempts = tonumber(redis.call('HINCRBY', hkey, 'attempts', 1))

      if attempts > max_attempts then
        redis.call('HSET', hkey, 'state', 'failed')
        redis.call('HINCRBY', 'nm:job:' .. job_id, 'failed', 1)
      else
        local deadline = now + ttl
        redis.call('HSET', hkey,
          'state', 'claimed', 'device_id', device, 'deadline', deadline)
        redis.call('ZADD', leases, deadline, sid)
        redis.call('HINCRBY', 'nm:job:' .. job_id, 'claimed', 1)
        return {
          sid,
          job_id,
          redis.call('HGET', hkey, 'idx'),
          redis.call('HGET', hkey, 'model_ref'),
          redis.call('HGET', hkey, 'tier'),
          tostring(deadline),
          redis.call('GET', 'nm:payload:' .. sid) or '',
        }
      end
    end
  end
end

return false
