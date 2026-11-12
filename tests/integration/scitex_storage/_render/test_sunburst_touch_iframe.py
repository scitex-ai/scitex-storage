"""Native touch at the observed public phone iframe's subpixel disk edge."""

import functools
import math
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.scitex_storage._render.test__sunburst import (
    sunburst_browser as sunburst_browser,
)


@pytest.mark.requires_fd
def test_touch_at_inner_disk_edge_in_scoped_iframe(tmp_path, sunburst_browser):
    # Arrange -- owned files, actual renderer, and the observed host iframe size.
    from scitex_storage._render import build_dir_hierarchy, build_dir_sunburst_html

    root = tmp_path / "data"
    for name, size in [
        ("Folder/first/a.bin", 8192),
        ("Folder/second/b.bin", 4096),
        ("Peer/c.bin", 2048),
    ]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
    chart = build_dir_sunburst_html(
        "Workspace files",
        build_dir_hierarchy("Workspace files", root),
        "Scanned /home/owned-fixture &middot; 2026-11-02T14:00:00 UTC",
    )
    (tmp_path / "chart.html").write_text(chart)
    (tmp_path / "index.html").write_text(
        '<!doctype html><iframe style="position:absolute;left:17px;top:166.375px;'
        'width:346px;height:506.390625px;border:0" sandbox="allow-scripts" '
        'src="/chart.html#Folder"></iframe>'
    )

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = sunburst_browser.new_page(
        viewport={"width": 390, "height": 844}, has_touch=True
    )
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
        outer = page.locator("iframe")
        frame = outer.element_handle().content_frame()
        frame.locator("#centre").wait_for()
        frame.evaluate("""document.addEventListener('pointerdown',e=>{
          const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(
            document.querySelector('#sb').getScreenCTM().inverse());
          window.hit={radius:Math.hypot(p.x-280,p.y-280),
            center:!!e.target.closest('#centre')};
        },true)""")
        geometry = frame.locator("#sb").evaluate("""s=>{
          const m=s.getScreenCTM(),p=new DOMPoint(280,349.8).matrixTransform(m);
          return {x:p.x,y:p.y,a:m.a,d:m.d,e:m.e,f:m.f};
        }""")
        box = outer.bounding_box()
        # The same scale/fractional translation reproduces native touch rounding.
        # Synthetic label metrics can shift the center by whole CSS pixels.
        exact_geometry = (
            box == {"x": 17, "y": 166.375, "width": 346, "height": 506.390625}
            and math.isclose(geometry["a"], 314 / 560)
            and math.isclose(geometry["d"], 314 / 560)
            and geometry["e"] == 16
            and geometry["f"] % 1 == 0.5
        )
        # Act -- native touch, including its compatibility click, not JS zoom.
        page.touchscreen.tap(
            round(geometry["x"] + box["x"]),
            math.floor(geometry["y"] + box["y"]),
        )
        expect(frame.locator("#crumb")).to_have_text("Workspace files")
        observation = frame.evaluate("window.hit")
        # Assert -- a genuinely interior event must retain the parent target.
        assert (
            exact_geometry,
            observation["radius"] < 70,
            observation["center"],
            frame.locator("#centre").get_attribute("aria-disabled"),
        ) == (True, True, True, "true")
    finally:
        page.close()
        server.shutdown()
        server.server_close()
