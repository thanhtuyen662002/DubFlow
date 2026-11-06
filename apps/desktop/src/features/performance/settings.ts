/** Cài đặt → Tài nguyên máy: detect the PC once, save its default, and apply the choice to every task. */
import { PERFORMANCE_KEY, clamp, describeChoice, describeMachine, firstRun, hostThreads, readPerformance,
  type Machine, type Performance, type ThreadMode } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing settings element: ${selector}`);
  return node;
};

function load(): Performance | null {
  try {
    return readPerformance(window.localStorage.getItem(PERFORMANCE_KEY));
  } catch {
    return null;
  }
}

function save(choice: Performance): boolean {
  try {
    window.localStorage.setItem(PERFORMANCE_KEY, JSON.stringify(choice));
    return true;
  } catch {
    return false;
  }
}

/** Returns `apply`, to call again whenever "Chỉ dùng CPU" changes. */
export function mountPerformanceSettings(invoke: Invoke, cpuOnly: () => boolean, showError: (text: string) => void): () => void {
  const summary = $("#machine-summary");
  const detail = $("#thread-detail");
  const mode = $<HTMLSelectElement>("#thread-mode");
  const count = $<HTMLInputElement>("#thread-count");
  let machine: Machine | null = null;
  let choice: Performance = load() ?? { mode: "auto", threads: 1 };

  function render(): void {
    mode.value = choice.mode;
    count.hidden = choice.mode !== "custom";
    count.max = String(machine?.threads ?? 256);
    count.value = String(choice.mode === "custom" ? choice.threads : hostThreads(choice, machine) || machine?.threads || 1);
    detail.textContent = describeChoice(choice, machine);
  }

  function apply(): void {
    void invoke("set_performance", { threads: hostThreads(choice, machine), cpuOnly: cpuOnly() }).catch(() => undefined);
  }

  function update(next: Performance): void {
    choice = next;
    if (!save(choice)) showError("Không lưu được lựa chọn tài nguyên máy trên máy này.");
    render();
    apply();
  }

  mode.addEventListener("change", () => {
    const picked = mode.value as ThreadMode;
    const threads = picked === "custom" && machine ? clamp(choice.mode === "custom" ? choice.threads : machine.recommended, machine) : choice.threads;
    update({ mode: picked, threads });
  });
  count.addEventListener("change", () => {
    const threads = machine ? clamp(Number(count.value), machine) : Math.max(1, Math.round(Number(count.value) || 1));
    update({ mode: "custom", threads });
  });

  render();
  apply();
  void invoke<Machine>("machine_profile").then((found) => {
    machine = found;
    summary.textContent = describeMachine(found);
    const saved = load();
    if (!saved) {
      // First run: the PC's own suggestion becomes the saved default.
      choice = firstRun(found);
      save(choice);
    } else {
      choice = saved;
    }
    render();
    apply();
  }).catch(() => {
    summary.textContent = "Không đọc được cấu hình máy; DubFlow dùng mặc định của từng tác vụ.";
  });
  return apply;
}
