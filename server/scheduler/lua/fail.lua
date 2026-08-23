-- A phone reporting it could not run a shard. Requeue if attempts remain,
-- otherwise burn it.
-- KEYS[1] = leases zset
-- ARGV = shard_id, device_id, retryable(0|1), max_attempts
-- returns {outcome, done, failed, shard_count, job_id}
--   outcome: 1 requeued, 2 failed permanently, 0 not ours

local sid, device = ARGV[1], ARGV[2]
local retryable = tonumber(ARGV[3]) == 1
local max_attempts = tonumber(ARGV[4])
local hkey = 'nm:shard:' .. sid
local job_id = redis.call('HGET', hkey, 'job_id')
if not job_id then return {0, 0, 0, 0, ''} end
local jkey = 'nm:job:' .. job_id

if redis.call('HGET', hkey, 'state') ~= 'claimed'
   or redis.call('HGET', hkey, 'device_id') ~= device then
  return {0, 0, 0, 0, job_id}
end

redis.call('ZREM', KEYS[1], sid)
redis.call('HDEL', hkey, 'deadline')
redis.call('HINCRBY', jkey, 'claimed', -1)
local attempts = tonumber(redis.call('HGET', hkey, 'attempts') or 0)
local outcome

if retryable and attempts < max_attempts then
  redis.call('HSET', hkey, 'state', 'queued')
  redis.call('HDEL', hkey, 'device_id')
  -- Head of the queue: a shard that has already burned an attempt is the most
  -- urgent thing in the fleet, not the least.
  redis.call('LPUSH', 'nm:q:' .. redis.call('HGET', hkey, 'tier'), sid)
  outcome = 1
else
  redis.call('HSET', hkey, 'state', 'failed')
  redis.call('HDEL', hkey, 'device_id')
  redis.call('HINCRBY', jkey, 'failed', 1)
  outcome = 2
end

return {outcome,
  tonumber(redis.call('HGET', jkey, 'done') or 0),
  tonumber(redis.call('HGET', jkey, 'failed') or 0),
  tonumber(redis.call('HGET', jkey, 'shard_count') or 0),
  job_id}
