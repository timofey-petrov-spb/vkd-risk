from streamlit.testing.v1 import AppTest
from pathlib import Path


def test_professional_grid_refinement_switch_runs_real_pipeline():
    at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/main.py'),default_timeout=120)
    at.run()
    at.sidebar.button(key='preset_quiet').click().run()
    at.sidebar.radio(key='level').set_value('Профессиональный').run()
    at.sidebar.checkbox(key='refine_grid').check().run()
    assert not at.exception
    captions='\n'.join(str(c.value) for c in at.caption)
    assert 'Проверка сходимости:' in captions
    assert 'не физической точности' in captions
    assert 'Шаг 5 с' in captions or 'Шаг 15 с' in captions
    body='\n'.join(str(e.value) for e in at.markdown)
    assert 'шаг трассы 5 с' in body or 'шаг трассы 15 с' in body
    assert 'шаг трассы 1 мин' not in body
