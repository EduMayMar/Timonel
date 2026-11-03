import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from timonel.classify import Classification
from timonel.engine import RunOptions
from timonel.ledger import Ledger
from timonel.router import build_plan
from timonel.web.server import App, make_server

from .conftest import Fakes, make_config

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e
SHOTS = os.environ.get("TIMONEL_SCREENSHOTS")
SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)


@pytest.fixture
def ui(fakes: Fakes, repo: Path, ledger: Ledger) -> Iterator[App]:
    app = App(make_config(), ledger, repo, environ={**fakes.env, "FAKE_CLASSIFICATION": SIMPLE})
    seeds = [
        (
            "copilot",
            "success",
            1,
            "Fix typo in the README installation section",
            "README.md=Install with pipx\n",
        ),
        ("codex", "crash", 2, "Add retry with backoff to the HTTP client", "client.py=retry = 3\n"),
        (
            "claude",
            "success",
            3,
            "Refactor the storage layer into a repository module",
            "storage.py=class Repo: ...\n",
        ),
    ]
    for agent, mode, complexity, task, edit in seeds:
        app.engine.environ.update({f"FAKE_MODE_{agent.upper()}": mode, "FAKE_EDIT": edit})
        plan = build_plan(
            app.config, Classification(complexity, "edit", "low", True, 1), available={agent}
        )
        app.engine.run(RunOptions(task=task, repo=repo, plan=plan))
        app.engine.environ.pop(f"FAKE_MODE_{agent.upper()}")
    app.engine.environ["FAKE_EDIT"] = "greeting.py=def greet(name):\n    return f'Hello, {name}!'\n"
    app.engine.environ["FAKE_SAY"] = "Added greeting.py with greet(name)."
    server = make_server(app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(
                executable_path=os.environ.get("TIMONEL_CHROMIUM") or None
            )
        except Exception as error:
            pytest.skip(f"chromium unavailable: {error}")
        yield instance
        instance.close()


def shot(page: object, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)  # type: ignore[attr-defined]


def open_ui(browser: object, app: App, tour: bool = False, **options: object) -> object:
    context = browser.new_context(
        viewport={"width": 1360, "height": 900}, device_scale_factor=2 if SHOTS else 1, **options
    )  # type: ignore[attr-defined]
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    page.errors = errors
    if not tour:
        page.add_init_script("localStorage.setItem('timonel.tour', 'done')")
    page.goto(f"http://127.0.0.1:{app.port}/?token={app.token}")
    return page


def test_full_flow(ui: App, browser: object, repo: Path) -> None:
    page = open_ui(browser, ui, locale="en-US")
    expect = sync_api.expect
    expect(page.get_by_role("heading", name="Panel")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.get_by_role("article", name="claude")).to_contain_text("ready")  # type: ignore[attr-defined]
    assert "token" not in page.url  # type: ignore[attr-defined]
    shot(page, "panel")

    page.keyboard.press("n")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="New task")).to_be_visible()  # type: ignore[attr-defined]
    page.get_by_role("textbox", name="Task").fill("Add a greet(name) helper in greeting.py")  # type: ignore[attr-defined]
    page.get_by_role("button", name="Preview plan").click()  # type: ignore[attr-defined]
    expect(page.get_by_text("complexity 1", exact=True)).to_be_visible()  # type: ignore[attr-defined]
    first_tier = page.get_by_role("region", name="Tier 1")  # type: ignore[attr-defined]
    expect(first_tier.locator(".rung")).to_have_count(3)
    first_tier.locator(".rung").nth(1).get_by_role("button", name="Move up").click()
    expect(first_tier.locator(".rung").first.locator("select")).to_have_value("claude")
    shot(page, "new-task")
    page.get_by_role("button", name="Launch").click()  # type: ignore[attr-defined]

    expect(page.get_by_role("heading", name="Timeline")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.locator(".timeline")).to_contain_text("succeeded", timeout=30000)  # type: ignore[attr-defined]
    expect(page.locator("pre.diff")).to_contain_text("+def greet(name):", timeout=15000)  # type: ignore[attr-defined]
    expect(page.locator(".timeline li").nth(1)).to_contain_text("claude")  # type: ignore[attr-defined]
    expect(page.locator(".badge").first).to_have_text("succeeded", timeout=15000)  # type: ignore[attr-defined]
    shot(page, "run")
    page.once("dialog", lambda dialog: dialog.accept())  # type: ignore[attr-defined]
    page.get_by_role("button", name="Apply to repository").click()  # type: ignore[attr-defined]
    expect(page.locator(".badge").first).to_have_text("applied", timeout=15000)  # type: ignore[attr-defined]
    assert "Hello" in (repo / "greeting.py").read_text()

    page.keyboard.press("h")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="History")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.locator("tbody tr")).to_have_count(4)  # type: ignore[attr-defined]
    page.get_by_label("Status").select_option("failed")  # type: ignore[attr-defined]
    expect(page.locator("tbody tr")).to_have_count(1)  # type: ignore[attr-defined]
    page.get_by_label("Status").select_option("")  # type: ignore[attr-defined]
    page.get_by_label("Search tasks").fill("greet")  # type: ignore[attr-defined]
    expect(page.locator("tbody tr")).to_have_count(1)  # type: ignore[attr-defined]
    shot(page, "history")

    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.keyboard.press("s")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Statistics")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.get_by_role("listitem", name="copilot: 100%")).to_be_visible()  # type: ignore[attr-defined]
    shot(page, "stats")

    page.keyboard.press("r")  # type: ignore[attr-defined]
    tier4 = page.locator('ol[data-tier="4"]')  # type: ignore[attr-defined]
    expect(tier4.locator(".rung")).to_have_count(2)
    page.locator('ol[data-tier="1"] .rung').first.drag_to(tier4)  # type: ignore[attr-defined]
    expect(tier4.locator(".rung")).to_have_count(3)
    page.get_by_role("button", name="Validate and save").click()  # type: ignore[attr-defined]
    expect(page.get_by_text("Saved:")).to_be_visible()  # type: ignore[attr-defined]
    assert len(ui.engine.config.tiers[4]) == 3
    shot(page, "routes")
    assert page.errors == []  # type: ignore[attr-defined]


def test_spanish_dark_mobile_and_keyboard(ui: App, browser: object) -> None:
    page = open_ui(browser, ui, locale="es-ES", color_scheme="dark")
    expect = sync_api.expect
    expect(page.get_by_role("heading", name="Panel")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Gasto de hoy")).to_be_visible()  # type: ignore[attr-defined]
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")  # type: ignore[attr-defined]
    assert background == "rgb(13, 23, 38)"
    shot(page, "panel-dark-es")
    page.keyboard.press("?")  # type: ignore[attr-defined]
    expect(page.get_by_role("dialog")).to_contain_text("Atajos de teclado")  # type: ignore[attr-defined]
    page.keyboard.press("Escape")  # type: ignore[attr-defined]
    page.keyboard.press("l")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Gasto de hoje")).to_be_visible()  # type: ignore[attr-defined]
    page.keyboard.press("l")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Spend today")).to_be_visible()  # type: ignore[attr-defined]
    page.keyboard.press("Tab")  # type: ignore[attr-defined]
    focused = page.evaluate("document.activeElement.textContent")  # type: ignore[attr-defined]
    assert "Skip" in focused
    page.set_viewport_size({"width": 390, "height": 844})  # type: ignore[attr-defined]
    width = page.evaluate("document.documentElement.scrollWidth")  # type: ignore[attr-defined]
    assert width <= 390
    shot(page, "panel-mobile")
    assert page.errors == []  # type: ignore[attr-defined]


def test_page_without_token_is_locked(ui: App, browser: object) -> None:
    context = browser.new_context()  # type: ignore[attr-defined]
    page = context.new_page()
    response = page.goto(f"http://127.0.0.1:{ui.port}/")
    assert response.status == 401
    status = page.evaluate("fetch('/api/state').then(r => r.status)")
    assert status == 401


def test_guided_tour_on_first_visit(ui: App, browser: object) -> None:
    page = open_ui(browser, ui, tour=True, locale="en-US")
    expect = sync_api.expect
    expect(page.get_by_role("dialog", name="1/5 · Panel")).to_be_visible()  # type: ignore[attr-defined]
    dialog = page.locator("#tour")  # type: ignore[attr-defined]
    expect(dialog).to_contain_text("Health of your agents")
    shot(page, "tour")
    for _ in range(4):
        dialog.get_by_role("button", name="Next").click()
    expect(page.get_by_role("dialog", name="5/5 · Settings")).to_be_visible()  # type: ignore[attr-defined]
    page.get_by_role("button", name="Done").click()  # type: ignore[attr-defined]
    expect(page.locator("#tour")).not_to_be_visible()  # type: ignore[attr-defined]
    page.reload()  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Panel")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.locator("#tour")).not_to_be_visible()  # type: ignore[attr-defined]
    assert page.errors == []  # type: ignore[attr-defined]


def test_setup_checklist_shows_commands_without_running_them(ui: App, browser: object) -> None:
    page = open_ui(browser, ui, locale="en-US")
    expect = sync_api.expect
    page.keyboard.press("i")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Setup checklist")).to_be_visible()  # type: ignore[attr-defined]
    expect(page.get_by_text("never asks for, sees or stores")).to_be_visible()  # type: ignore[attr-defined]
    claude = page.locator('.setup-item[data-key="claude"]')  # type: ignore[attr-defined]
    expect(claude).to_contain_text("ready")
    deepseek = page.locator('.setup-item[data-key="deepseek"]')  # type: ignore[attr-defined]
    expect(deepseek.get_by_role("button", name="Add API key")).to_have_count(0)
    openai = page.locator('.setup-item[data-key="openai"]')  # type: ignore[attr-defined]
    openai.get_by_role("button", name="Add API key").click()
    expect(openai.locator("code")).to_contain_text("timonel keys set OPENAI_API_KEY")
    expect(page.locator('.setup-item[data-key="ollama"]')).to_contain_text("ready")  # type: ignore[attr-defined]
    expect(page.locator('.setup-item[data-key="openai"]')).to_contain_text("needs API key")  # type: ignore[attr-defined]
    expect(page.locator('.setup-item[data-key="openai"]')).to_contain_text("never to edit files")  # type: ignore[attr-defined]
    shot(page, "setup")
    assert page.errors == []  # type: ignore[attr-defined]


def test_settings_form_validates_previews_and_saves(ui: App, browser: object) -> None:
    page = open_ui(browser, ui, locale="en-US")
    expect = sync_api.expect
    page.keyboard.press("c")  # type: ignore[attr-defined]
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()  # type: ignore[attr-defined]
    page.get_by_label("Search options").fill("ui.density")  # type: ignore[attr-defined]
    option = page.locator('.option[data-key="ui.density"]')  # type: ignore[attr-defined]
    expect(option).to_be_visible()
    option.locator("select").select_option("compact")
    expect(option).to_contain_text("changed")
    expect(page.locator("pre.console")).to_contain_text("density: compact")  # type: ignore[attr-defined]
    expect(page.get_by_text("Valid", exact=True)).to_be_visible()  # type: ignore[attr-defined]
    page.get_by_label("Search options").fill("verification.timeout")  # type: ignore[attr-defined]
    page.get_by_label("Show advanced options").check()  # type: ignore[attr-defined]
    timeout = page.locator('.option[data-key="verification.timeout_s"] input')  # type: ignore[attr-defined]
    timeout.fill("-5")
    timeout.dispatch_event("change")
    expect(page.locator(".problems")).to_contain_text("verification.timeout_s")  # type: ignore[attr-defined]
    page.locator('.option[data-key="verification.timeout_s"]').get_by_role(
        "button", name="Back to default"
    ).click()  # type: ignore[attr-defined]
    expect(page.get_by_text("Valid", exact=True)).to_be_visible()  # type: ignore[attr-defined]
    expect(page.locator("pre.console")).not_to_contain_text("verification")  # type: ignore[attr-defined]
    shot(page, "settings")
    page.get_by_role("button", name="Validate and save").click()  # type: ignore[attr-defined]
    expect(page.get_by_text("Saved:")).to_be_visible()  # type: ignore[attr-defined]
    assert ui.engine.config.ui.density == "compact"
    assert page.errors == []  # type: ignore[attr-defined]


AUDIT = """() => {
  const problems = [];
  const name = (node) => (node.getAttribute("aria-label") || node.getAttribute("aria-labelledby")
    || (node.id && document.querySelector(`label[for="${node.id}"]`)?.textContent)
    || node.closest("label")?.textContent || node.textContent || node.getAttribute("title") || "").trim();
  for (const node of document.querySelectorAll("button, a[href], input, select, textarea")) {
    if (node.closest("[hidden], dialog:not([open])")) continue;
    if (node.type === "hidden") continue;
    if (!name(node) && !node.getAttribute("placeholder")) problems.push(`no name: ${node.outerHTML.slice(0, 80)}`);
  }
  for (const node of document.querySelectorAll("img")) if (!node.hasAttribute("alt")) problems.push("img without alt");
  let last = 0;
  for (const node of document.querySelectorAll("main h1, main h2, main h3")) {
    const level = Number(node.tagName[1]);
    if (level > last + 1) problems.push(`heading jump to ${node.tagName}: ${node.textContent}`);
    last = level;
  }
  const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
  for (const id of new Set(ids)) if (ids.filter((other) => other === id).length > 1) problems.push(`duplicate id ${id}`);
  if (!document.documentElement.lang) problems.push("no lang");
  return problems;
}"""


@pytest.mark.parametrize("language", ["en-US", "es-ES"])
def test_every_view_passes_the_accessibility_checks(
    ui: App, browser: object, language: str
) -> None:
    page = open_ui(browser, ui, locale=language)
    for view in ("panel", "new", "history", "stats", "routes", "setup", "settings"):
        page.goto(f"http://127.0.0.1:{ui.port}/#/{view}")  # type: ignore[attr-defined]
        page.wait_for_selector("main h1:not(:text('Loading…')):not(:text('Cargando…'))")  # type: ignore[attr-defined]
        page.wait_for_load_state("networkidle")  # type: ignore[attr-defined]
        assert page.evaluate(AUDIT) == [], view  # type: ignore[attr-defined]
    page.reload()  # type: ignore[attr-defined]
    page.keyboard.press("Tab")  # type: ignore[attr-defined]
    assert page.evaluate("document.activeElement.classList.contains('skip')")  # type: ignore[attr-defined]
    assert page.errors == []  # type: ignore[attr-defined]
