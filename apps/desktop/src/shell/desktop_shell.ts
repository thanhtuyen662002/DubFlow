import { toQueueViewModel } from "../features/queue/view_model.ts";
import { type QueueController, type QueueSnapshot } from "../features/queue/model.ts";

/**
 * Framework-neutral shell model for the eventual Tauri/React view.
 * Native file pickers and supervisor transport are injected at the boundary;
 * this module only renders user-facing queue state and never infers progress.
 */
export type DesktopShellModel = {
  snapshot: QueueSnapshot;
  view: ReturnType<typeof toQueueViewModel>;
  dispose: () => void;
};

export function connectDesktopShell(
  queue: QueueController,
  onChange: (model: DesktopShellModel) => void,
): DesktopShellModel {
  let current: DesktopShellModel | null = null;
  const disposeSubscription = queue.subscribe((snapshot) => {
    const next: DesktopShellModel = {
      snapshot,
      view: toQueueViewModel(snapshot),
      dispose: () => disposeSubscription(),
    };
    current = next;
    onChange(next);
  });
  if (!current) throw new Error("Queue subscription did not publish an initial snapshot");
  return current;
}
