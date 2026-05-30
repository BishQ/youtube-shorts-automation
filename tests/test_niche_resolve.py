from shorts_pipeline.planner.niche_resolve import resolve_niche


def test_historical_figure_maps_to_documentary() -> None:
    assert resolve_niche("historical_figure") == "documentary"


def test_unknown_falls_back_to_documentary() -> None:
    assert resolve_niche("general") == "documentary"
    assert resolve_niche("not_a_niche") == "documentary"


def test_crime_stays_crime() -> None:
    assert resolve_niche("crime") == "crime"
