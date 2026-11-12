/* Storage leaf header picker placement (operator CCT4707).
 *
 * The SDK AppHeader primitive owns the header row and creates the canonical
 * `.stx-app-header__slot--project-selector` slot client-side on
 * DOMContentLoaded; the picker itself is server-rendered by the
 * `{% scitex_project_picker %}` tag (authed user + host provider only — see
 * `templates/scitex_storage/_project_picker.html`). This module moves that
 * element into the slot after both auto-mounts have run.
 *
 * Ordering is by registration, not by guessing: this module is included AFTER
 * the `app-header.js` / `project-selector.js` modules, and deferred modules
 * execute in document order before DOMContentLoaded fires — so its listener
 * runs AFTER theirs, and the header (and its slot) and the mounted picker
 * both exist when it fires. Moving a mounted picker node preserves its
 * component state (listeners live on the element, not its position).
 *
 * Fail-soft: no picker (anon/standalone) or no single mounted header means
 * do nothing — the page keeps whatever the server rendered.
 */
(function () {
    function place() {
        var picker = document.querySelector("[data-stx-project-picker]");
        if (!picker) return;
        var api = window.stxAppHeader;
        var header =
            api && typeof api.get === "function" ? api.get() : null;
        if (header && header.projectSlot) {
            header.projectSlot.appendChild(picker);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", place);
    } else {
        place();
    }
})();
