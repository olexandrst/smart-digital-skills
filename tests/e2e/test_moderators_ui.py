"""Модерація за розділами в браузері (DEV-0 … DEV-04): пакет без SKILL.md,
погодження правок з автором, архів і повернення з архіву, журнал рішень."""
import io
import zipfile

from tests.e2e.helpers import expect, login


def _relogin(page, base, username):
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    login(page, base, username)


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _submit(page, name):
    """Автор подає матеріал у розділ менеджера; повертає id."""
    return page.evaluate("""async (name) => {
        const sec = (await api('/catalog/sections')).find(s => s.name === 'Матеріали для тестів');
        return (await api('/catalog/resources', { method: 'POST', body: {
          resource_type: 'prompt', name, description: 'Опис для тесту.', body: 'Текст промпту',
          status: 'published', section_id: sec.id } })).id; }""", name)


def test_package_without_skill_md_shows_error_at_once(page, live_url):
    login(page, live_url, "author")
    page.click("#cat-add")
    page.click('.wiz-kind[data-kind="skill"]')
    page.set_input_files("#wiz-file", files=[{
        "name": "my-skill.zip", "mimeType": "application/zip",
        "buffer": _zip_bytes({"README.md": "# Навичка"})}])
    expect(page.locator(".wiz-pkg-err")).to_contain_text("немає файла SKILL.md")
    expect(page.locator("#wiz-next")).to_be_disabled()
    # Правильний пакет знімає помилку й показує, що прочитано зі SKILL.md.
    page.set_input_files("#wiz-file", files=[{
        "name": "my-skill.zip", "mimeType": "application/zip",
        "buffer": _zip_bytes({"my-skill/SKILL.md":
                              "---\nname: Протокол наради\ndescription: Опис\nversion: 2.0.0\n---\n"})}])
    expect(page.locator(".wiz-pkg-err")).to_have_count(0)
    expect(page.locator("#wiz-drop")).to_contain_text("Навичка «Протокол наради», версія 2.0.0")
    expect(page.locator("#wiz-next")).to_be_enabled()
    # Майстер підказує, хто розгляне подання в обраному розділі.
    page.select_option("#wiz-section", label="Матеріали для тестів")
    expect(page.locator("#wiz-who")).to_contain_text("модератор Менеджер Розділу")


def test_moderator_edit_needs_author_agreement(page, live_url):
    login(page, live_url, "author")
    rid = _submit(page, "Промпт для узгодження правок")
    _relogin(page, live_url, "manager")
    page.evaluate("""(id) => api(`/catalog/resources/${id}`, { method: 'PATCH',
        body: { description: 'Опис, уточнений модератором.' } })""", rid)
    page.click('#mode-seg [data-mode="admin"]')
    row = page.locator(f'.content-queue .res-row[data-id="{rid}"]')
    row.locator('[data-act="approve"]').click()
    dialog = page.locator(".agree-dlg")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Погодьте правки з автором")
    expect(page.locator("#agree-pub")).to_be_disabled()
    page.check("#agree-ok")
    page.click("#agree-pub")
    expect(row).to_have_count(0)
    status = page.evaluate("async (id) => (await api(`/catalog/resources/${id}`)).status", rid)
    assert status == "published"
    log = page.evaluate("async (id) => api(`/catalog/resources/${id}/review-log`)", rid)
    assert log[0]["actor_role"] == "moderator"
    assert "погоджено з автором" in log[0]["note"]


def test_archive_and_restore_from_card(page, live_url):
    login(page, live_url, "author")
    rid = _submit(page, "Матеріал для архіву")
    _relogin(page, live_url, "manager")
    page.evaluate("(id) => api(`/catalog/resources/${id}/status`, { method: 'POST', "
                  "body: { status: 'published' } })", rid)
    page.evaluate("() => ensureHubData(true).then(() => go('hub:all'))")
    page.locator("#cat-grid article.mdc", has_text="Матеріал для архіву").click()
    modal = page.locator(".md-modal")
    expect(modal.locator(".sm-modinfo")).to_contain_text("Менеджер Розділу (модератор розділу)")
    modal.locator('[data-m="archive"]').click()
    page.fill("#rsn-note", "Застаріло")
    page.click("#rsn-ok")
    expect(page.locator("#toast")).to_contain_text("Перенесено в архів")
    # Архівне шукається в «Модерації» фільтром «Архів» і повертається з картки.
    page.click('#mode-seg [data-mode="admin"]')
    page.click('.res-health [data-f="archived"]')
    archived = page.locator(".mine-list .res-row", has_text="Матеріал для архіву")
    expect(archived).to_have_count(1)
    archived.locator('[data-act="open"]').click()
    page.locator('.md-modal [data-m="restore"]').click()
    expect(page.locator("#toast")).to_contain_text("Повернуто з архіву")
    status = page.evaluate("async (id) => (await api(`/catalog/resources/${id}`)).status", rid)
    assert status == "published"
    page.evaluate("() => { contribFilter = 'all'; }")
