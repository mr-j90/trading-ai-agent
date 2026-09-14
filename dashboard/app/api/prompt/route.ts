// The saved input/output of one decision cycle: runs/<strategy>/prompts/<YYYYMMDDTHHMMSS>Z.json
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runDir, strategy } from "../lib";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const q = new URL(req.url).searchParams;
  const name = q.get("strategy") ?? "main";
  strategy(name); // throws on unknown
  const stamp = (q.get("ts") ?? "").replace(/[-:]/g, "").slice(0, 15); // 2026-09-14T21:15:50.24+00:00 -> 20260914T211550
  if (!/^\d{8}T\d{6}$/.test(stamp)) return Response.json({ error: "bad ts" }, { status: 400 });
  const p = join(runDir(name), "prompts", `${stamp}Z.json`);
  if (!existsSync(p)) return Response.json({ error: "no saved prompt for this cycle (older cycles predate prompt logging)" }, { status: 404 });
  return new Response(readFileSync(p, "utf8"), { headers: { "content-type": "application/json" } });
}
