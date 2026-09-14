// Halt or resume a strategy by writing/removing its HALT flag. The only file the dashboard writes.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { runDir, strategy } from "../lib";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const { strategy: name, halt } = await req.json();
  strategy(name); // throws on unknown
  const dir = runDir(name);
  mkdirSync(dir, { recursive: true });
  if (halt) writeFileSync(join(dir, "HALT"), `manual halt from dashboard ${new Date().toISOString()}`);
  else rmSync(join(dir, "HALT"), { force: true });
  return Response.json({ ok: true, halted: !!halt });
}
