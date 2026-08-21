from app.export_xlsx import _cell_value


def test_cell_value_serializes_nested_values():
    value = _cell_value({'why': [{'factor': 'topic', 'score': 1.0}]})
    assert isinstance(value, str)
    assert 'topic' in value
