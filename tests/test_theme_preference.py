from megamozg.shared_state import SharedState


def test_glamour_is_a_valid_shared_theme_in_both_modes(tmp_path):
    store = SharedState(tmp_path)
    for revision, mode in enumerate(("dark", "light")):
        value = {"theme": "glamour", "mode": mode}
        result = store.set_preferences({"request_id": f"glamour-{mode}", "expected_revision": revision,
                                        "patch": {"work:theme": value}})
        assert result["values"]["work:theme"] == value
        assert store.preferences()["values"]["work:theme"] == value
