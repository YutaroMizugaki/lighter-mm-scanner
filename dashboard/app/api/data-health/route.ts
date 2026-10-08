import { getCollectorStatusResult } from "@/lib/api";
import { collectionHealth } from "@/lib/dataHealth";

export const dynamic = "force-dynamic";

export async function GET() {
  const result = await getCollectorStatusResult();
  return Response.json(collectionHealth(result.ok ? result.data : null), {
    headers: { "Cache-Control": "no-store" },
  });
}
