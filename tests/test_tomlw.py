import tomllib

from phil.tomlw import dump_toml


def test_an_empty_table_gets_no_header():
    assert dump_toml({"run": {"x": 1}, "providers": {}}) == "[run]\nx = 1\n"


def test_a_table_of_only_none_values_gets_no_header():
    assert dump_toml({"project": {"test_cmd": None}}) == ""


def test_a_parent_of_only_sub_tables_gets_no_header_of_its_own():
    text = dump_toml({"providers": {"lab": {"kind": "openai"}, "empty": {}}})
    assert text == '[providers.lab]\nkind = "openai"\n'
    assert tomllib.loads(text) == {"providers": {"lab": {"kind": "openai"}}}


def test_a_table_with_leaves_and_sub_tables_keeps_its_header():
    text = dump_toml({"budget": {"x": 1, "critic": {"max_input_tokens": 5}}})
    assert text == "[budget]\nx = 1\n\n[budget.critic]\nmax_input_tokens = 5\n"
