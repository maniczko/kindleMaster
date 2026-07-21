# Production admission — memory pressure scenario

Related issue: #376  
Implementation PR: #394  
Capacity calibration: #381

## Purpose

Verify that KindleMaster refuses new OCR/conversion work when container memory headroom is unsafe, without interrupting already accepted jobs or exposing internal resource values to users.

## Preconditions

- Staging runs in a Linux container with a known cgroup memory limit.
- `KINDLEMASTER_MEMORY_ADMISSION=1`.
- Record:
  - `KINDLEMASTER_MIN_MEMORY_AVAILABLE_BYTES`;
  - `KINDLEMASTER_MIN_MEMORY_AVAILABLE_RATIO`;
  - Railway memory allocation;
  - worker count.
- Prepare one normal small PDF and one long-running OCR fixture.

## Scenario 1 — safe headroom

1. Confirm cgroup available memory is above both configured thresholds.
2. Upload the small PDF through `/convert/start`.

Expected:

- Request is accepted normally.
- No `memory_capacity_exceeded` event is emitted.
- The job enters the durable queue.

## Scenario 2 — cgroup v2 low-memory rejection

1. Run or simulate load until `memory.max - memory.current` falls below the byte or ratio threshold.
2. Submit a new conversion.

Expected:

- HTTP 503.
- Stable error code `memory_capacity_exceeded`.
- `Retry-After: 30`.
- No source file, durable job or worker command is created.
- Response does not expose total or available memory values.
- Existing accepted jobs continue according to their own lifecycle.

## Scenario 3 — cgroup fallback

1. In an isolated test container, make cgroup v2 data unavailable.
2. Provide cgroup v1 memory files and repeat the safe/blocked cases.
3. Make both cgroup versions unavailable and provide `/proc/meminfo`.

Expected:

- Measurement source is selected in this order:
  1. cgroup v2;
  2. cgroup v1;
  3. `/proc/meminfo`.
- The same threshold behavior applies for every measurable source.

## Scenario 4 — telemetry unavailable

1. In a controlled local test only, make all memory telemetry paths unavailable.
2. Submit a normal conversion.

Expected:

- Local runtime remains usable rather than failing closed solely because telemetry is unavailable.
- Observability records source `unavailable`.
- Public staging must not be accepted until memory telemetry is measurable.

## Scenario 5 — recovery after pressure falls

1. Trigger a low-memory rejection.
2. Release memory pressure.
3. Wait for available bytes and ratio to exceed thresholds.
4. Resubmit the same valid file.

Expected:

- New request is accepted without restarting the API.
- The rejected request did not consume a queue slot.
- Only the accepted request produces a job and artifact.

## Exit criteria

- cgroup-aware measurement is confirmed on the real Railway resource class;
- low-memory request creates zero durable work;
- response is stable and retryable;
- already accepted jobs are not silently deleted;
- thresholds and observed headroom are attached to #376 and #381.
