"""Real SVG fill and native drill-down for a one-child scoped directory."""

import pytest
from playwright.sync_api import expect

from tests.scitex_storage._render.test__sunburst import (
    sunburst_browser as sunburst_browser,
)

pytestmark = pytest.mark.requires_fd


@pytest.fixture
def single_child_chart(tmp_path):
    from scitex_storage._render import build_dir_hierarchy, build_dir_sunburst_html

    target = tmp_path / "scope" / "Only" / "Nested" / "owned.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * 1024)
    hierarchy = build_dir_hierarchy("Owned scope", tmp_path / "scope")
    chart = tmp_path / "single.html"
    chart.write_text(
        build_dir_sunburst_html("Owned scope", hierarchy, "Owned browser fixture")
    )
    return chart


def test_full_annulus_has_fill_all_around_and_preserves_its_hole(
    single_child_chart, sunburst_browser
):
    # Arrange
    page = sunburst_browser.new_page(viewport={"width": 800, "height": 600})
    page.goto(single_child_chart.as_uri())
    # Act -- use the real browser SVG geometry engine, not bounding-box clicks.
    geometry = page.evaluate("""() => {
      const path=document.querySelector('#sb path.seg');
      const contains=(r,a)=>path.isPointInFill(
        new DOMPoint(280+r*Math.cos(a),280+r*Math.sin(a)));
      const samples=Array.from({length:16},(_,i)=>contains(110,(i+.25)*Math.PI/8));
      return [samples,contains(0,0),contains(69,0),contains(160,0),
        path.getAttribute('d').match(/A/g)?.length,ROOT.value];
    }""")
    page.close()
    # Assert -- the full first ring remains an annulus with its actual value.
    assert geometry == [[True] * 16, False, False, False, 4, 1024]


def test_partial_sector_keeps_its_existing_arc_geometry(
    single_child_chart, sunburst_browser
):
    # Arrange
    page = sunburst_browser.new_page()
    page.goto(single_child_chart.as_uri())
    # Act -- call the renderer's actual pure geometry function in real SVG.
    geometry = page.evaluate("""() => {
      const p=document.createElementNS('http://www.w3.org/2000/svg','path');
      p.setAttribute('d',arcPath(70,159,0,Math.PI/2));
      document.querySelector('#sb').appendChild(p);
      const contains=(r,a)=>p.isPointInFill(
        new DOMPoint(280+r*Math.cos(a),280+r*Math.sin(a)));
      return [p.getAttribute('d').match(/A/g)?.length,
        contains(110,Math.PI/4),contains(110,Math.PI),contains(69,Math.PI/4),
        contains(160,Math.PI/4),arcPath(70,159,0,TAU-1e-12).match(/A/g)?.length];
    }""")
    page.close()
    # Assert -- ordinary and nearly-full partial sectors still use two arcs.
    assert geometry == [2, True, False, False, False, 2]


@pytest.mark.parametrize(
    "device,width,height", [("mouse", 1280, 566), ("touchscreen", 390, 420)]
)
def test_single_child_rings_drill_down_and_center_stays_in_scope(
    single_child_chart, sunburst_browser, device, width, height
):
    # Arrange
    page = sunburst_browser.new_page(
        viewport={"width": width, "height": height}, has_touch=True
    )
    page.goto(single_child_chart.as_uri())
    action = page.mouse.click if device == "mouse" else page.touchscreen.tap

    def activate(radius):
        point = page.locator("#sb").evaluate(
            """(s,r)=>{
              const p=new DOMPoint(280+r,280).matrixTransform(s.getScreenCTM());
              return {x:p.x,y:p.y};
            }""",
            radius,
        )
        action(point["x"], point["y"])

    # Act -- actual ring interiors, followed by the existing parent disk.
    activate(110)
    expect(page.locator("#crumb")).to_have_text("Owned scope / Only")
    activate(110)
    expect(page.locator("#crumb")).to_have_text("Owned scope / Only / Nested")
    activate(0)
    expect(page.locator("#crumb")).to_have_text("Owned scope / Only")
    activate(0)
    expect(page.locator("#crumb")).to_have_text("Owned scope")
    activate(0)
    state = page.locator("#centre").get_attribute("aria-disabled")
    crumb = page.locator("#crumb").inner_text()
    expect(page.locator("#tip")).to_have_css("opacity", "0")
    # Capture the verified root after native interaction has rendered the scene.
    page.screenshot(path=str(single_child_chart.parent / f"annulus-{device}.png"))
    page.close()
    # Assert -- no filesystem ancestor exists outside the loaded scope.
    assert (state, crumb) == ("true", "Owned scope")
