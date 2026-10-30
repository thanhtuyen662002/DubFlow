import { sourceCreditForJob, validateSourceCredit, type SourceCredit } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type CreditJob = { id: string; outputPath: string | null; originUrl?: string; status: { state: string } };

/** Keep loads and saves attached to their selected output, even across polling. */
export function mountSourceCredit(invoke: Invoke): { select(job: CreditJob | null): void } {
  const $ = <T extends HTMLElement>(selector: string): T => {
    const node = document.querySelector<T>(selector);
    if (!node) throw new Error(`Missing ${selector}`);
    return node;
  };
  const form = $<HTMLFormElement>("#source-credit");
  const fields = $<HTMLFieldSetElement>("#credit-fields");
  const state = $("#credit-state");
  const input = (name: string) => $<HTMLInputElement | HTMLSelectElement>(`#credit-${name}`);
  let selection = "", outputPath = "", request = 0;

  function show(credit: SourceCredit): void {
    for (const name of ["creator", "platform", "url", "rights", "note"] as const) input(name).value = credit[name];
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (fields.disabled || !outputPath) return;
    const credit = { schema_version: 1, creator: input("creator").value, platform: input("platform").value,
      url: input("url").value, rights: input("rights").value, note: input("note").value };
    const error = validateSourceCredit(credit);
    if (error) { state.textContent = error; return; }
    const current = request, path = outputPath;
    fields.disabled = true;
    state.textContent = "Đang lưu…";
    try {
      await invoke("source_credit_set", { outputPath: path, credit });
      if (current === request) state.textContent = "Đã lưu nguồn & quyền.";
    } catch (error) {
      if (current === request) state.textContent = `Không lưu được: ${String(error).slice(0, 180)}`;
    } finally {
      if (current === request) fields.disabled = false;
    }
  });

  return {
    select(job) {
      const path = job?.status.state === "COMPLETED" ? job.outputPath : null;
      form.hidden = !path;
      const next = path ? `${job!.id}\n${path}` : "";
      if (next === selection) return;
      selection = next;
      outputPath = path ?? "";
      const current = ++request;
      fields.disabled = true;
      form.reset();
      state.textContent = "";
      if (!path) return;
      state.textContent = "Đang đọc thông tin nguồn…";
      void invoke<unknown>("source_credit_get", { outputPath: path }).then((value) => {
        if (current !== request) return;
        show(sourceCreditForJob(value, job?.originUrl));
        fields.disabled = false;
        state.textContent = "";
      }).catch((error) => {
        if (current === request) state.textContent = `Không đọc được: ${String(error).slice(0, 180)}`;
      });
    },
  };
}
