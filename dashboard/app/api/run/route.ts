// Manually trigger one run of the agent. Same script, same lock, same journal as the launchd jobs.
import { execFile } from "node:child_process";
import { join } from "node:path";

export const dynamic = "force-dynamic";
const ROOT = join(process.cwd(), "..");
const UV = join(process.env.HOME ?? "", ".local/bin/uv");

export async function POST(req: Request) {
  const { job } = await req.json().catch(() => ({ job: "cycle" }));
  const args = ["run", "--frozen", "main.py", ...(job === "guard" ? ["--guard"] : [])];
  return new Promise<Response>((resolve) => {
    execFile(UV, args, { cwd: ROOT, timeout: 180_000 }, (err, stdout, stderr) => {
      resolve(Response.json({ ok: !err, output: (stdout + stderr).trim() || (err ? String(err) : "") }, { status: err ? 500 : 200 }));
    });
  });
}
