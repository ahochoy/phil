// Reveal and wire every [data-copy] button. Without the Clipboard API the buttons stay hidden
// and the command text stays select-all.
export function setupCopyButtons(root: ParentNode): void {
  if (!navigator.clipboard) return;
  for (const button of root.querySelectorAll<HTMLButtonElement>("button[data-copy]")) {
    const label = button.textContent ?? "Copy";
    let timer: number | undefined;
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy ?? "");
      } catch {
        return;
      }
      button.textContent = "Copied";
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        button.textContent = label;
      }, 1500);
    });
  }
}
