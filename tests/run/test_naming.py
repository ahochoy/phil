import pytest

from phil.contracts import Goal
from phil.run.naming import MAX_CHARS, SLUG, built_name, run_name

OBJECTIVE = "Add a CTA section below the newsletter form"


def named(name, objective=OBJECTIVE):
    return Goal(objective=objective, name=name)


def test_a_valid_intake_name_is_kept():
    assert run_name(named("newsletter-cta"), "r-4f1c") == "newsletter-cta"


@pytest.mark.parametrize("raw,expected", [
    ("  Newsletter CTA ", "newsletter-cta"),
    ("signup_form_fix", "signup-form-fix"),
    ("newsletter--cta", "newsletter-cta"),
    ("Newsletter _ CTA", "newsletter-cta"),
])
def test_intake_names_are_normalised(raw, expected):
    assert run_name(named(raw), "r-4f1c") == expected


@pytest.mark.parametrize("bad", [None, "", "cta", "a-b-c-d", "newsletter-cta!", "-cta-", "x" * 20 + "-" + "y" * 20])
def test_a_bad_intake_name_is_replaced_by_the_built_one(bad):
    assert run_name(named(bad), "r-4f1c") == "cta-section-below"


def test_stop_words_are_dropped_and_three_words_kept():
    assert built_name("Add a subtract function to calc", "r-1") == "subtract-function-calc"


def test_too_few_words_left_takes_the_first_words_of_the_objective():
    assert built_name("Fix the CSS", "r-1") == "fix-the-css"


def test_punctuation_is_removed_from_words():
    assert built_name("Update README.md, please!", "r-1") == "readmemd-please"


def test_no_usable_words_gives_the_run_id_name():
    assert run_name(Goal(objective="修复 登录"), "r-4f1c") == "run-4f1c"
    assert built_name("the a", "r-9a0b") == "the-a"  # only stop words: the objective's own first words


def test_a_long_name_is_cut_at_a_hyphen():
    assert built_name("Add documentation translations everywhere", "r-1") == "documentation-translations"


def test_a_word_too_long_to_cut_gives_the_run_id_name():
    assert built_name("x" * 40 + " " + "y" * 3, "r-4f1c") == "run-4f1c"


@pytest.mark.parametrize("objective", [
    OBJECTIVE, "Fix the CSS", "修复 登录", "Add documentation translations everywhere", "Make it so, number one",
])
def test_every_name_is_a_slug(objective):
    name = run_name(Goal(objective=objective), "r-4f1c")
    assert SLUG.match(name) and len(name) <= MAX_CHARS
