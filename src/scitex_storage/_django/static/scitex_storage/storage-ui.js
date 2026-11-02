/* Storage consumes the shared picker event; page scope is not a global default. */
document.addEventListener("stx-project-selector:change", (event) => {
  if (!(event.target instanceof Element) || !event.target.closest("#stx-storage-app")) return;
  const choice = event.detail;
  if (!choice || choice.scope !== "user" && choice.scope !== "project") return;
  const url = new URL(window.location.href);
  if (choice.scope === "user" && choice.id === null) {
    url.searchParams.delete("project");
    url.searchParams.set("scope", "all");
  } else if (choice.scope === "project" && typeof choice.id === "string" && choice.id) {
    url.searchParams.set("project", choice.id);
    url.searchParams.set("scope", "project");
  } else return;
  window.location.assign(url.href);
});

function sendTheme(frame) {
  const style = getComputedStyle(document.documentElement);
  const colors = {};
  for (const [target, role] of Object.entries({
    "--bg": "--bg-page", "--panel": "--bg-surface", "--text": "--text-primary",
    "--muted": "--text-secondary", "--ring": "--border-default", "--link": "--accent",
  })) colors[target] = style.getPropertyValue(role).trim();
  frame.contentWindow?.postMessage({
    type: "scitex-storage-theme", theme: document.documentElement.dataset.theme, colors,
  }, "*"); // Sandboxed documents have an opaque origin; no user data is sent.
}
document.addEventListener("load", (event) => {
  if (event.target instanceof HTMLIFrameElement && event.target.matches(".stx-storage-sunburst")) sendTheme(event.target);
}, true);
new MutationObserver(() => {
  document.querySelectorAll(".stx-storage-sunburst").forEach(sendTheme);
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
document.querySelectorAll(".stx-storage-sunburst").forEach(sendTheme);
