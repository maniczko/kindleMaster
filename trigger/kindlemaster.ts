import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { task } from "@trigger.dev/sdk/v3";

const execFileAsync = promisify(execFile);

interface KindleMasterConversionPayload {
  job_id: string;
  replay: {
    command: {
      name: string;
      args?: unknown[];
      kwargs?: Record<string, unknown>;
    };
    context?: Record<string, unknown>;
  };
  timeout_seconds?: number;
}

export const kindlemasterConversion = task({
  id: "kindlemaster-conversion",
  maxDuration: 1_800,
  retry: {
    maxAttempts: 3,
    minTimeoutInMs: 10_000,
    maxTimeoutInMs: 60_000,
    factor: 2,
    randomize: true,
  },
  run: async (payload: KindleMasterConversionPayload) => {
    const command = payload.replay?.command;
    if (!payload.job_id || command?.name !== "convert") {
      throw new Error("Invalid KindleMaster conversion payload.");
    }

    const python = process.env.KINDLEMASTER_PYTHON ?? "python";
    const result = await execFileAsync(
      python,
      ["kindlemaster.py", "status"],
      {
        timeout: Math.max(1, payload.timeout_seconds ?? 1_800) * 1_000,
        maxBuffer: 1024 * 1024,
      },
    );

    return {
      job_id: payload.job_id,
      command: command.name,
      status_probe: result.stdout.slice(0, 16_000),
    };
  },
});
