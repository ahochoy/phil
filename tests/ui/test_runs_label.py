from types import SimpleNamespace

from phil.ui.runs import label_for, run_label


def test_first_and_later_mentions():
    assert label_for("r-4f1c", "newsletter-cta", first=True) == "newsletter-cta (r-4f1c)"
    assert label_for("r-4f1c", "newsletter-cta", first=False) == "newsletter-cta"


def test_a_run_with_no_name_is_its_id():
    for name in (None, ""):
        assert label_for("r-4f1c", name, first=True) == "r-4f1c"
        assert label_for("r-4f1c", name, first=False) == "r-4f1c"


def test_run_label_reads_a_record():
    assert run_label(SimpleNamespace(run_id="r-1", name="map-section"), first=True) == "map-section (r-1)"
