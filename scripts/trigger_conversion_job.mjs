import { tasks } from "@trigger.dev/sdk/v3";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

const raw = await readStdin();
const payload = raw.trim() ? JSON.parse(raw) : {};
const taskId = payload.task_id || process.env.KINDLEMASTER_TRIGGER_TASK_ID || "kindlemaster-conversion";

if (!process.env.TRIGGER_SECRET_KEY) {
  throw new Error("TRIGGER_SECRET_KEY is required to submit Trigger.dev jobs.");
}

const handle = await tasks.trigger(taskId, payload, {
  idempotencyKey: payload.job_id ? `kindlemaster-${payload.job_id}` : undefined,
  maxAttempts: payload.retry_policy?.max_attempts ?? 3,
  maxDuration: payload.timeout_seconds ?? 1800,
  tags: ["kindlemaster", payload.replay?.command?.name ?? "conversion"],
  metadata: {
    job_id: payload.job_id,
    source_type: payload.replay?.command?.kwargs?.source_type,
    profile: payload.replay?.command?.kwargs?.profile,
  },
});

process.stdout.write(
  JSON.stringify({
    provider: "trigger.dev",
    external_id: handle.id,
    task_id: taskId,
  }),
);
