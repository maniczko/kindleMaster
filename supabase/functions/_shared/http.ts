import { corsHeaders } from "./cors.ts";

export function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

export function getErrorMessage(error: unknown) {
  const message = String((error as Error)?.message || error || "Unknown error").trim();
  return message.length > 500 ? `${message.slice(0, 497)}...` : message;
}
