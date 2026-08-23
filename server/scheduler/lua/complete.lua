-- Record a finished shard. Ownership-checked: a phone that lost its lease
-- mid-flight and finished anyway is told it lost, and is not paid twice.
--
-- KEYS[1] = leases zset
-- ARGV = shard_id, device_id, sealed_result
-- returns {status, done, failed, shard_count, job_id}
--   status: 1 accepted, 0 not ours / already settled

local sid, device, result = ARGV[1], ARGV[2], ARGV[3]
local hkey = 'nm:shard:' .. sid
local job_id = redis.call('HGET', hkey, 'job_id')
if not job_id then return {0, 0, 0, 0, ''} end

local jkey = 'nm:job:' .. job_id
local state = redis.call('HGET', hkey, 'state')
local owner = redis.call('HGET', hkey, 'device_id')

if state ~= 'claimed' or owner ~= device then
  return {0,
    tonumber(redis.call('HGET', jkey, 'done') or 0),
    tonumber(redis.call('HGET', jkey, 'failed') or 0),
    tonumber(redis.call('HGET', jkey, 'shard_count') or 0),
    job_id}
end

redis.call('HSET', hkey, 'state', 'done')
redis.call('HDEL', hkey, 'deadline')
redis.call('ZREM', KEYS[1], sid)
redis.call('SET', 'nm:result:' .. sid, result)
redis.call('DEL', 'nm:payload:' .. sid)          -- inputs are spent; stop storing them
redis.call('HINCRBY', jkey, 'claimed', -1)
local done = redis.call('HINCRBY', jkey, 'done', 1)

return {1, done,
  tonumber(redis.call('HGET', jkey, 'failed') or 0),
  tonumber(redis.call('HGET', jkey, 'shard_count') or 0),
  job_id}
