import json
from kagnode.web import kagnode_website_url


def test_public_link_follows_operator_file_and_never_exposes_local_settings(app, tmp_path, monkeypatch):
    settings = tmp_path / 'links.json'
    monkeypatch.setenv('KAGNODE_SERVICE_LINKS_FILE', str(settings))
    settings.write_text(json.dumps({'public': 'https://new.example', 'operator': 'http://192.168.0.10:8082'}))
    page = app.test_client().get('/').get_data(as_text=True)
    assert page.count('href="https://new.example"') == 2
    assert '192.168.0.10:8082' not in page
    settings.write_text(json.dumps({'public': 'https://next.example'}))
    assert kagnode_website_url() == 'https://next.example'


def test_invalid_or_missing_shared_link_has_safe_fallback(tmp_path, monkeypatch):
    settings = tmp_path / 'links.json'
    monkeypatch.setenv('KAGNODE_SERVICE_LINKS_FILE', str(settings))
    monkeypatch.delenv('KAGNODE_WEBSITE_URL', raising=False)
    assert kagnode_website_url() == 'https://nebulacoder.com'
    for value in ['http://192.168.0.10', 'https://127.0.0.1', 'https://box.lan', 'https://person@new.example', 'https://new.example/path', 'javascript:alert(1)', None]:
        settings.write_text(json.dumps({'public': value}))
        assert kagnode_website_url() == 'https://nebulacoder.com'
    settings.write_text('invalid JSON')
    assert kagnode_website_url() == 'https://nebulacoder.com'
