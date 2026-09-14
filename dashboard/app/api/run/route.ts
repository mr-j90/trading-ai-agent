// Manually trigger one run of a strategy. Same script, same lock, same journal as the launchd jobs.
import { execFile } from "node:child_process";
import { join } from "node:path";
import { ROOT, strategy } from "../lib";

export const dynamic = "force-dynamic";
const UV = join(process.env.HOME ?? "", ".local/bin/uv");

export async function POST(req: Request) {
  const { job = "cycle", strategy: name = "main" } = await req.json().catch(() => ({}));
  strategy(name); // throws on unknown
  // manual decision runs skip the Telegram summary so the scheduled 15:30 run stays the only sender
  const args = ["run", "--frozen", "main.py", "--strategy", name, ...(job === "guard" ? ["--guard"] : ["--no-summary"])];
  return new Promise<Response>((resolve) => {
    execFile(UV, args, { cwd: ROOT, timeout: 180_000 }, (err, stdout, stderr) => {
      resolve(Response.json({ ok: !err, output: (stdout + stderr).trim() || (err ? String(err) : "") }, { status: err ? 500 : 200 }));
    });
  });
}
