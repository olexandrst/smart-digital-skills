"""Хвиля 5 у браузері: колір, розмір і виділення в редакторі тексту, порожні
стани «Нещодавніх» і пошуку."""
from tests.e2e.helpers import expect, login


def _select(page, text):
    page.evaluate("""(t) => {
        const area = document.querySelector('#wiz-editor .rte-area');
        const w = document.createTreeWalker(area, NodeFilter.SHOW_TEXT);
        while (w.nextNode()) {
            const i = w.currentNode.nodeValue.indexOf(t);
            if (i >= 0) {
                const r = document.createRange();
                r.setStart(w.currentNode, i); r.setEnd(w.currentNode, i + t.length);
                const s = getSelection(); s.removeAllRanges(); s.addRange(r); return;
            }
        }
    }""", text)


def _source(page):
    page.click('#wiz-editor [data-mode="source"]')
    value = page.locator("#wiz-editor .rte-src").input_value()
    page.click('#wiz-editor [data-mode="visual"]')
    return value


def test_color_size_underline_and_marker(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="instruction"]')
    page.click("#wiz-editor .rte-area")
    page.keyboard.type("Важливо: перевірте строки й суму перед підписанням.")
    _select(page, "Важливо")
    page.click('#wiz-editor [data-pop="color"]')
    page.click('#wiz-editor .rte-swatch[data-color="red"]')
    _select(page, "строки")
    page.click('#wiz-editor [data-cmd="mark"]')
    _select(page, "суму")
    page.click('#wiz-editor [data-cmd="underline"]')
    _select(page, "перед підписанням")
    page.click('#wiz-editor [data-pop="size"]')
    page.click('#wiz-editor [data-size="large"]')
    assert _source(page) == ('<span class="tc-red">Важливо</span>: перевірте <mark>строки</mark> '
                             'й <u>суму</u> <span class="ts-large">перед підписанням</span>.')
    # «Без кольору» прибирає колір, решта лишається.
    _select(page, "Важливо")
    page.click('#wiz-editor [data-pop="color"]')
    page.click('#wiz-editor .rte-pop-txt[data-color=""]')
    assert _source(page).startswith("Важливо: перевірте <mark>строки</mark>")

    # Картка показує те саме оформлення.
    page.click("#wiz-next")
    page.fill("#wiz-name", "Оформлений текст")
    page.fill("#wiz-desc", "Перевірка оформлення.")
    page.click("#wiz-draft")
    doc = page.locator(".md-modal .md-doc")
    expect(doc.locator("mark")).to_have_text("строки")
    expect(doc.locator("u")).to_have_text("суму")
    expect(doc.locator(".ts-large")).to_have_text("перед підписанням")


def test_word_colors_map_to_palette_and_prompt_has_no_formatting(page, live_url):
    login(page, live_url)
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="instruction"]')
    page.click("#wiz-editor .rte-area")
    page.evaluate("""() => {
        const dt = new DataTransfer();
        dt.setData('text/html', `<html xmlns:o="urn:schemas-microsoft-com:office:office"><body>
          <p class=MsoNormal><span style='color:#C00000'>Червоний</span> і
          <span style='color:#00B050'>зелений</span>, <span style='color:black'>чорний</span>,
          <span style='background:yellow;mso-highlight:yellow'>маркер</span></p></body></html>`);
        dt.setData('text/plain', 'x');
        document.querySelector('#wiz-editor .rte-area').dispatchEvent(
            new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""")
    assert _source(page) == ('<span class="tc-red">Червоний</span> і <span class="tc-green">зелений</span>, '
                             'чорний, <mark>маркер</mark>')
    # У промпті кнопок оформлення немає, а оформлення з тексту (документ,
    # інструкція, перетворена на промпт) прибирається.
    page.once("dialog", lambda d: d.accept())
    page.click("#wiz-rekind")
    page.click('.wiz-kind[data-kind="prompt"]')
    expect(page.locator("#wiz-editor .rte-fmt")).to_be_hidden()
    assert page.evaluate("""() => RichEditor.stripFormatting(
        '<mark><span class="tc-red">Червоний</span></mark> і <u>{дані}</u> <документ>')""") \
        == "Червоний і {дані} <документ>"


def test_empty_recent_and_search_states(page, live_url):
    login(page, live_url)
    page.evaluate("() => { localStorage.removeItem('hub-recent'); go('hub:recent'); }")
    empty = page.locator("#cat-grid .cat-empty")
    expect(empty).to_contain_text("Тут з'являться матеріали, які ви відкривали")
    page.click("#empty-all")
    expect(page.locator(".page-h1")).to_have_text("Матеріали")
    page.fill("#global-q", "жирафиквантові")
    # «Поділитися ідеєю» поки приховано — лишається «Спитати помічника».
    acts = page.locator(".cat-empty-acts button")
    expect(acts).to_have_text(["Спитати помічника"])
    box = acts.first.bounding_box()
    assert box["height"] == 34
    grid = page.locator("#cat-grid").bounding_box()
    middle = box["x"] + box["width"] / 2
    assert abs(middle - (grid["x"] + grid["width"] / 2)) < 20   # по центру під текстом
