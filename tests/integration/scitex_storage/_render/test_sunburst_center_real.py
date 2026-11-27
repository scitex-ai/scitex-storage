"""Actual mouse, touch and keyboard over the Usage renderer's scoped tree."""

import pytest

from tests.scitex_storage._render.test__sunburst import (
    sunburst_browser as sunburst_browser,
)


def _center_hit(page, activate, offset, pixel_snap=False):
    point = page.locator("#sb").evaluate(
        """(svg, input) => {
          const {offset,snap}=input;
          const p=new DOMPoint(280+offset[0],280+offset[1])
            .matrixTransform(svg.getScreenCTM());
          // Touch compatibility events quantize to device pixels. Choose the
          // nearest representable INTERIOR pixel rather than round outside.
          const inward=(value,delta)=>delta<0?Math.ceil(value):
            delta>0?Math.floor(value):Math.round(value);
          return snap?{x:inward(p.x,offset[0]),y:inward(p.y,offset[1])}:{x:p.x,y:p.y};
        }""",
        {"offset": offset, "snap": pixel_snap},
    )
    activate(point["x"], point["y"])


def _probe_center_navigation(page, url, activate, pixel_snap):
    observations = []
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.add_init_script("""document.addEventListener('pointerdown',event=>{
      const svg=document.querySelector('#sb');if(!svg)return;
      const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());
      window.centrePointer={inside:!!event.target.closest('#centre'),r:Math.hypot(p.x-280,p.y-280)};
    },true);""")
    for offset in [
        (-69.8, 0),
        (69.8, 0),
        (0, -69.8),
        (0, 69.8),
        (-68, 0),
        (68, 0),
        (0, -68),
        (0, 68),
        (-48, -48),
        (48, 48),
        (0, -22),
        (0, -2),
        (0, 16),
        (0, 0),
    ]:
        page.goto(url)
        _center_hit(page, activate, (110, 0), pixel_snap)
        _center_hit(page, activate, (110, 0), pixel_snap)
        nested = page.locator("#crumb").inner_text()
        _center_hit(page, activate, offset, pixel_snap)
        observations.append(
            (
                nested,
                page.locator("#crumb").inner_text(),
                page.evaluate(
                    "window.centrePointer.inside && window.centrePointer.r < 70"
                ),
            )
        )
    page.goto(url + "#Folder>Nested")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    parent = page.locator("#crumb").inner_text()
    page.keyboard.press("Space")
    root = page.locator("#crumb").inner_text()
    page.keyboard.press("Enter")
    _center_hit(page, activate, (0, 0), pixel_snap)
    disabled = page.evaluate("""() => [
      document.querySelector('#centre')?.getAttribute('aria-disabled'),
      document.querySelector('#centre')?.getAttribute('tabindex')]
    """)
    root_noop = page.locator("#crumb").inner_text()
    page.goto(url + "#Folder>Nested")
    page.locator("#crumb a").first.click()
    breadcrumb = page.locator("#crumb").inner_text()
    hostile = []
    for fragment in [
        "..>outside",
        "%2e%2e>%2Foutside",
        "%2Foutside",
        "%",
        "%252e%252e",
    ]:
        page.goto(url + "#" + fragment)
        _center_hit(page, activate, (0, 0), pixel_snap)
        hostile.append(page.locator("#crumb").inner_text())
    return (
        observations,
        parent,
        root,
        disabled,
        root_noop,
        breadcrumb,
        hostile,
        errors,
        [u for u in requests if u.split("#", 1)[0] != url],
    )


@pytest.fixture
def usage_center_chart(tmp_path):
    """A genuine bounded tree rendered only from owned temporary files."""
    from scitex_storage._render import build_dir_hierarchy, build_dir_sunburst_html

    for relative, size in [
        ("Folder/Nested/data.bin", 8192),
        ("Folder/Nested/second.bin", 4096),
        ("Folder/Sibling/data.bin", 4096),
        ("Peer/data.bin", 2048),
    ]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
    tree = build_dir_hierarchy("Scoped project", tmp_path)
    chart = tmp_path / "center.html"
    chart.write_text(
        build_dir_sunburst_html("Scoped project", tree, "Owned browser fixture")
    )
    return chart


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "device,action,width,height",
    [("mouse", "click", 1280, 566), ("touchscreen", "tap", 390, 420)],
)
def test_usage_center_whole_disk_navigates_parent_without_scope_escape(
    usage_center_chart, sunburst_browser, device, action, width, height
):
    # Arrange
    chart = usage_center_chart
    page = sunburst_browser.new_page(
        viewport={"width": width, "height": height}, has_touch=True
    )
    activate = getattr(getattr(page, device), action)
    # Act -- real inputs cover labels and the outer gap in the inner disk.
    result = _probe_center_navigation(page, chart.as_uri(), activate, action == "tap")
    page.goto(chart.as_uri() + "#Folder>Nested")
    page.reload()
    page.mouse.move(0, 0)
    page.screenshot(path=str(chart.parent / ("center-" + device + ".png")))
    page.close()
    # Assert -- exactly one loaded parent, and no ancestor request at the scoped root.
    assert result == (
        [("Scoped project / Folder / Nested", "Scoped project / Folder", True)] * 14,
        "Scoped project / Folder",
        "Scoped project",
        ["true", "-1"],
        "Scoped project",
        "Scoped project",
        ["Scoped project"] * 5,
        [],
        [],
    )
