"""Each export follows the pulse semantics of the page linking to it."""
import html
import json
import re

from tests.test_v151_r6_csv_pointer_values import _state, _csv
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def test_comparison_and_qubits_export_the_values_they_render(tmp_path):
    live = tmp_path / 'live'
    live.mkdir()
    state = _state()
    ops = state['qubits']['q1']['xy']['operations']
    ops['x180'] = '#./x180_Square'
    ops['x180_Square'] = {'amplitude': .75, 'length': 24}
    (live / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (live / 'wiring.json').write_text('{}', encoding='utf-8')
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    client.post('/load', data={'folder': str(live)})
    comparison = client.get('/table?props=x180_amplitude&props=x180_length').get_data(as_text=True)
    qubits = client.get('/qubits').get_data(as_text=True)
    for page, amplitude, length in ((comparison, .25, 40), (qubits, .75, 24)):
        link = html.unescape(re.search(r'href="(/export\?[^\"]+)"', page).group(1))
        _headers, rows = _csv(client.get(link))
        assert float(rows['q1']['x180_amplitude']) == amplitude, link
        assert float(rows['q1']['x180_length [ns]']) == length, link
        # Verify the actual rendered row, not only the export URL's intent.
        row = next(r for r in re.findall(r'<tr\b[^>]*>.*?</tr>', page, re.S)
                   if re.search(r'>q1</(?:strong|a)>', r))
        cells = [re.sub('<[^>]+>', '', c).strip() for c in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)]
        assert any(c and c.replace('.', '', 1).isdigit() and float(c) == amplitude for c in cells), cells
        assert any(c and c.isdigit() and float(c) == length for c in cells), cells
